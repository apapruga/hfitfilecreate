from __future__ import annotations

import csv
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
FILE_TYPE_WORKOUT = 5
MANUFACTURER_DEVELOPMENT = 255
SPORT_RUNNING = 1
DURATION_TIME = 0
DURATION_OPEN = 5
TARGET_SPEED = 0
TARGET_OPEN = 2
INTENSITY_ACTIVE = 0
INTENSITY_REST = 1
INTENSITY_WARMUP = 2
INTENSITY_COOLDOWN = 3
INTENSITY_RECOVERY = 4
INTENSITY_INTERVAL = 5
FIT_BASE_ENUM = 0x00
FIT_BASE_STRING = 0x07
FIT_BASE_UINT16 = 0x84
FIT_BASE_UINT32 = 0x86
FIT_BASE_UINT32Z = 0x8C
INVALID_UINT32 = 0xFFFFFFFF
WORKOUT_NAME_SIZE = 64
STEP_NAME_SIZE = 32


@dataclass
class Step:
    name: str
    step_type: str
    duration_sec: int
    pace_min: Optional[str] = None
    pace_max: Optional[str] = None
    avg_pace: Optional[str] = None
    repeats: int = 1


CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400,
    0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401,
    0x5000, 0x9C01, 0x8801, 0x4400,
]


def fit_crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc ^= tmp ^ CRC_TABLE[byte & 0xF]
        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc ^= tmp ^ CRC_TABLE[(byte >> 4) & 0xF]
    return crc & 0xFFFF


def fit_timestamp(dt: Optional[datetime] = None) -> int:
    if dt is None:
        dt = datetime.now(timezone.utc)
    return int((dt - FIT_EPOCH).total_seconds())


def encode_string(value: str, size: int) -> bytes:
    raw = value.encode("utf-8", errors="ignore")
    if len(raw) >= size:
        raw = raw[: size - 1]
    return raw + b"\x00" * (size - len(raw))


def parse_pace_to_seconds(pace_str: Optional[str]) -> Optional[int]:
    if pace_str is None:
        return None
    pace_str = pace_str.strip()
    if not pace_str:
        return None
    if ":" not in pace_str:
        raise ValueError(f"Неверный формат pace={pace_str!r}. Ожидается M:SS")
    minutes_str, seconds_str = pace_str.split(":", 1)
    minutes = int(minutes_str)
    seconds = int(seconds_str)
    if minutes < 0:
        raise ValueError(f"Минуты не могут быть отрицательными: {pace_str!r}")
    if seconds < 0 or seconds >= 60:
        raise ValueError(f"Секунды должны быть от 00 до 59: {pace_str!r}")
    total = minutes * 60 + seconds
    if total <= 0:
        raise ValueError(f"Темп должен быть > 0: {pace_str!r}")
    return total


def pace_seconds_to_mps(sec_per_km: int) -> float:
    return 1000.0 / sec_per_km


def build_speed_range_from_pace(
    pace_min: Optional[str],
    pace_max: Optional[str],
    avg_pace: Optional[str],
    default_tolerance_sec: int = 5,
) -> Optional[Tuple[int, int]]:
    fast_sec = parse_pace_to_seconds(pace_min)
    slow_sec = parse_pace_to_seconds(pace_max)
    avg_sec = parse_pace_to_seconds(avg_pace)
    if fast_sec is not None and slow_sec is not None:
        fastest = min(fast_sec, slow_sec)
        slowest = max(fast_sec, slow_sec)
    elif avg_sec is not None:
        fastest = max(1, avg_sec - default_tolerance_sec)
        slowest = avg_sec + default_tolerance_sec
    else:
        return None
    speed_low = pace_seconds_to_mps(slowest)
    speed_high = pace_seconds_to_mps(fastest)
    return int(round(speed_low * 1000)), int(round(speed_high * 1000))


def step_type_to_intensity(step_type: str) -> int:
    key = step_type.strip().lower()
    mapping = {
        "warmup": INTENSITY_WARMUP,
        "interval": INTENSITY_INTERVAL,
        "recovery": INTENSITY_RECOVERY,
        "cooldown": INTENSITY_COOLDOWN,
        "active": INTENSITY_ACTIVE,
        "rest": INTENSITY_REST,
        "easy": INTENSITY_ACTIVE,
        "work": INTENSITY_INTERVAL,
        "fast": INTENSITY_INTERVAL,
        "recover": INTENSITY_RECOVERY,
    }
    if key not in mapping:
        raise ValueError(
            f"Неизвестный step_type={step_type!r}. "
            f"Допустимо: warmup, interval, recovery, cooldown, active, rest, easy, work, fast, recover"
        )
    return mapping[key]


def read_csv_steps(csv_path: str | Path) -> List[Step]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV файл не найден: {path}")
    steps: List[Step] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"step_name", "step_type", "duration_sec", "repeats"}
        actual = set(reader.fieldnames or [])
        missing = required - actual
        if missing:
            raise ValueError(
                f"В CSV не хватает колонок: {sorted(missing)}. Обязательные: {sorted(required)}"
            )
        for row_num, row in enumerate(reader, start=2):
            try:
                step_name = (row.get("step_name") or "").strip()
                step_type = (row.get("step_type") or "").strip()
                duration_sec_raw = (row.get("duration_sec") or "").strip()
                repeats_raw = (row.get("repeats") or "1").strip()
                pace_min = (row.get("pace_min") or "").strip() or None
                pace_max = (row.get("pace_max") or "").strip() or None
                avg_pace = (row.get("avg_pace") or "").strip() or None
                if not step_name:
                    raise ValueError("step_name пустой")
                if not step_type:
                    raise ValueError("step_type пустой")
                if not duration_sec_raw:
                    raise ValueError("duration_sec пустой")
                duration_sec = int(duration_sec_raw)
                repeats = int(repeats_raw)
                if duration_sec < 0:
                    raise ValueError("duration_sec не может быть отрицательным")
                if repeats <= 0:
                    raise ValueError("repeats должен быть >= 1")
                _ = step_type_to_intensity(step_type)
                _ = build_speed_range_from_pace(pace_min=pace_min, pace_max=pace_max, avg_pace=avg_pace)
                steps.append(Step(step_name, step_type, duration_sec, pace_min, pace_max, avg_pace, repeats))
            except Exception as e:
                raise ValueError(f"Ошибка в строке CSV {row_num}: {e}") from e
    if not steps:
        raise ValueError("CSV пустой: не найдено ни одного шага тренировки")
    return steps


def expand_steps(steps: List[Step]) -> List[Step]:
    expanded: List[Step] = []
    for step in steps:
        for i in range(step.repeats):
            step_name = f"{step.name} {i+1}/{step.repeats}" if step.repeats > 1 else step.name
            expanded.append(Step(step_name, step.step_type, step.duration_sec, step.pace_min, step.pace_max, step.avg_pace, 1))
    return expanded


class FitWriter:
    def __init__(self) -> None:
        self.records = bytearray()

    def add_definition(self, local_msg_num: int, global_msg_num: int, fields: List[tuple[int, int, int]]) -> None:
        header = 0x40 | (local_msg_num & 0x0F)
        self.records.append(header)
        self.records.append(0x00)
        self.records.append(0x00)
        self.records.extend(struct.pack("<H", global_msg_num))
        self.records.append(len(fields))
        for field_num, size, base_type in fields:
            self.records.extend(bytes([field_num, size, base_type]))

    def add_data(self, local_msg_num: int, payload: bytes) -> None:
        header = local_msg_num & 0x0F
        self.records.append(header)
        self.records.extend(payload)

    def build_file(self) -> bytes:
        data = bytes(self.records)
        header_wo_crc = bytearray()
        header_wo_crc.append(14)
        header_wo_crc.append(0x20)
        header_wo_crc.extend(struct.pack("<H", 2180))
        header_wo_crc.extend(struct.pack("<I", len(data)))
        header_wo_crc.extend(b".FIT")
        header_crc = fit_crc(bytes(header_wo_crc))
        header = bytes(header_wo_crc) + struct.pack("<H", header_crc)
        file_without_crc = header + data
        file_crc = fit_crc(file_without_crc)
        return file_without_crc + struct.pack("<H", file_crc)


def build_file_id_message(now_ts: int) -> bytes:
    return b"".join([
        struct.pack("<B", FILE_TYPE_WORKOUT),
        struct.pack("<H", MANUFACTURER_DEVELOPMENT),
        struct.pack("<H", 1),
        struct.pack("<I", 1),
        struct.pack("<I", now_ts),
    ])


def build_workout_message(workout_name: str, num_steps: int) -> bytes:
    return b"".join([
        struct.pack("<B", SPORT_RUNNING),
        struct.pack("<I", 0),
        struct.pack("<H", num_steps),
        encode_string(workout_name, WORKOUT_NAME_SIZE),
    ])


def build_workout_step_message(index: int, step: Step, default_tolerance_sec: int = 5) -> bytes:
    intensity = step_type_to_intensity(step.step_type)
    if step.duration_sec == 0:
        duration_type = DURATION_OPEN
        duration_value = 0
    else:
        duration_type = DURATION_TIME
        duration_value = step.duration_sec * 1000
    speed_range = build_speed_range_from_pace(
        pace_min=step.pace_min,
        pace_max=step.pace_max,
        avg_pace=step.avg_pace,
        default_tolerance_sec=default_tolerance_sec,
    )
    if speed_range is None:
        target_type = TARGET_OPEN
        target_value = 0
        custom_target_low = INVALID_UINT32
        custom_target_high = INVALID_UINT32
    else:
        custom_target_low, custom_target_high = speed_range
        target_type = TARGET_SPEED
        target_value = 0
    return b"".join([
        struct.pack("<H", index),
        encode_string(step.name, STEP_NAME_SIZE),
        struct.pack("<B", duration_type),
        struct.pack("<I", duration_value),
        struct.pack("<B", target_type),
        struct.pack("<I", target_value),
        struct.pack("<I", custom_target_low),
        struct.pack("<I", custom_target_high),
        struct.pack("<B", intensity),
    ])


def create_fit_workout_bytes(steps: List[Step], workout_name: str = "Running Workout", default_tolerance_sec: int = 5) -> bytes:
    now_ts = fit_timestamp()
    writer = FitWriter()
    writer.add_definition(0, 0, [(0, 1, FIT_BASE_ENUM), (1, 2, FIT_BASE_UINT16), (2, 2, FIT_BASE_UINT16), (3, 4, FIT_BASE_UINT32Z), (4, 4, FIT_BASE_UINT32)])
    writer.add_data(0, build_file_id_message(now_ts))
    writer.add_definition(1, 26, [(4, 1, FIT_BASE_ENUM), (5, 4, FIT_BASE_UINT32Z), (6, 2, FIT_BASE_UINT16), (8, WORKOUT_NAME_SIZE, FIT_BASE_STRING)])
    writer.add_data(1, build_workout_message(workout_name, len(steps)))
    writer.add_definition(2, 27, [(254, 2, FIT_BASE_UINT16), (0, STEP_NAME_SIZE, FIT_BASE_STRING), (1, 1, FIT_BASE_ENUM), (2, 4, FIT_BASE_UINT32), (3, 1, FIT_BASE_ENUM), (4, 4, FIT_BASE_UINT32), (5, 4, FIT_BASE_UINT32), (6, 4, FIT_BASE_UINT32), (7, 1, FIT_BASE_ENUM)])
    for idx, step in enumerate(steps):
        writer.add_data(2, build_workout_step_message(idx, step, default_tolerance_sec))
    return writer.build_file()


def convert_csv_to_fit(csv_path: str | Path, fit_path: str | Path, workout_name: str = "Running Workout", default_tolerance_sec: int = 5, verbose: bool = True) -> Path:
    raw_steps = read_csv_steps(csv_path)
    expanded_steps = expand_steps(raw_steps)
    fit_bytes = create_fit_workout_bytes(expanded_steps, workout_name=workout_name, default_tolerance_sec=default_tolerance_sec)
    fit_path = Path(fit_path)
    fit_path.write_bytes(fit_bytes)
    if verbose:
        print(f"Готово: {fit_path.resolve()}")
        print(f"Исходных строк CSV: {len(raw_steps)}")
        print(f"Итоговых шагов FIT: {len(expanded_steps)}")
        print(f"Название тренировки: {workout_name}")
    return fit_path


def create_sample_csv(csv_path: str | Path = "sample_workout.csv") -> Path:
    csv_path = Path(csv_path)
    rows = [
        ["step_name", "step_type", "duration_sec", "pace_min", "pace_max", "avg_pace", "repeats"],
        ["Разминка", "warmup", "720", "7:20", "7:35", "", "1"],
        ["Ускорение", "interval", "20", "5:40", "5:55", "", "4"],
        ["Восстановление", "recovery", "40", "7:40", "8:20", "", "4"],
        ["Основной интервал", "interval", "120", "6:00", "6:10", "", "6"],
        ["Восстановление", "recovery", "120", "7:40", "8:10", "", "6"],
        ["Заминка", "cooldown", "600", "7:20", "7:50", "", "1"],
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return csv_path
