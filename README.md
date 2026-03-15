# Telegram bot: CSV -> FIT for running workouts

This bot accepts a CSV file in Telegram and returns a `.fit` workout file.

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

Example:

```csv
step_name,step_type,duration_sec,pace_min,pace_max,avg_pace,repeats
Разминка,warmup,720,7:20,7:35,,1
Ускорение,interval,20,5:40,5:55,,4
Восстановление,recovery,40,7:40,8:20,,4
Основной интервал,interval,120,6:00,6:10,,6
Восстановление,recovery,120,7:40,8:10,,6
Заминка,cooldown,600,7:20,7:50,,1
```
