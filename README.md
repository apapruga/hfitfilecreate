# Telegram bot: CSV -> FIT for running workouts

This bot accepts either a CSV file or plain CSV text in Telegram and returns a `.fit` workout file.

## Input modes

The bot supports two explicit input modes:
- CSV file upload
- CSV text pasted directly into the chat

The user selects the mode in advance with `/mode`, `/mode_file`, or `/mode_text`.

## CSV format

Required columns:
- `step_name`
- `step_type`
- `duration_sec`
- `repeats`

Optional pace columns:
- `pace_min` — fastest allowed pace, e.g. `5:40`
- `pace_max` — slowest allowed pace, e.g. `5:55`
- `avg_pace` — fallback if you don't want to specify a range

Optional heart rate columns:
- `hr_min` — lower bound of target heart rate in bpm, e.g. `150`
- `hr_max` — upper bound of target heart rate in bpm, e.g. `165`
- `avg_hr` — fallback if you don't want to specify a range

For each row, use either pace columns or heart rate columns. Mixing both target types in one step is not supported.

Example:

```csv
step_name,step_type,duration_sec,pace_min,pace_max,avg_pace,hr_min,hr_max,avg_hr,repeats
Разминка,warmup,720,7:20,7:35,,,,,1
Ускорение,interval,20,,,,165,175,,4
Восстановление,recovery,40,,,,130,145,,4
Основной интервал,interval,120,6:00,6:10,,,,,6
Восстановление,recovery,120,,,,,,140,6
Заминка,cooldown,600,7:20,7:50,,,,,1
```
