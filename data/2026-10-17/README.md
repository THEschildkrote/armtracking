# Session data, 2026-10-17

Everything recorded or calibrated on the Windows laptop during the 2026-10-17 session, copied here so
it can be picked up from another machine. The live copies stay where the code reads them (and most of
them are gitignored there because they are per-person or per-setup), so **copy a file back into place
only if your setup matches**: same arm, same glasses wearer, same board layout, same side-camera spot.

No LeRobot teleop episodes were recorded this session. The arm runs scripted picks driven by the side
camera (see `scene/live_objects.py`, `arm/scripted.py`), so there is no ACT/SmolVLA training dataset yet.

## eye/ - eye camera (Board A, XIAO ESP32S3, OpenIris UVC)
| file | what |
|---|---|
| `eye_test2..6.csv`, `eye_extremes.csv` | pupil-detector recordings from `eye.cmd`: one row per frame - `t, x, y` (pupil centre, px), ellipse `major, minor, angle`, `openness`, `valid`, `darkness`, `contrast`, `usable`, `closed`, `reason`, and `event` (blinks: `long` / `double` / ...). `eye_extremes.csv` is looking far up/down/left/right, used to stop glances counting as blinks; the others are blink and spread tests |
| `detector_config.json` | detector settings that produced them (`eye/config.json`: ROI, min circularity, ...) |

## gaze/
| file | what |
|---|---|
| `calib.json` | gaze calibration: pupil -> front-camera pixels (screen mode, 9.8 px LOO). Per wearer: belongs in `gaze/calib.json` |

## arm/ - SO-101
| file | what | where it lives |
|---|---|---|
| `lerobot_calibration/robots/so_follower/my_follower.json` | follower calibration (COM4) | `~/.cache/huggingface/lerobot/calibration/robots/so_follower/` |
| `lerobot_calibration/teleoperators/so_leader/my_leader.json` | leader calibration (COM7) | `~/.cache/huggingface/lerobot/calibration/teleoperators/so_leader/` |
| `home_pose.json` | rest pose (raw ticks) | `arm/home_pose.json` |
| `table_to_robot.json` | table mm -> robot frame: rigid fit (25.5 mm, the arm model's error), affine (8.7 mm) and the homography picks use; includes the 4 touched marker points | `arm/table_to_robot.json` |
| `cameras.json` | which USB camera is which (by usb_id) | `arm/cameras.json` (tracked) |

## scene/ - table and objects
| file | what |
|---|---|
| `config.json` | marker layout, table mm: 0 far-left (0, 241.3), 1 far-right (619.6, 241.3), 2 near-left (0, 0), 3 near-right (619.6, 0) - note 2 and 3 are the other way round from the original plan |
| `side_table_hold.json` | side camera's all-four-marker homography (5.5 mm) for its current mounting - goes in `logs/` |
| `reach_map.json` | where the arm can pick pointing down (share of grip angles per 50 mm cell) - goes in `logs/` |
| `classes.json` | object classes: name, YOLOE prompt, grip height, taught spot/angle, learned colour signature, grip offset, position correction |
| `objects.json` | last live scene written by `objects.cmd` |

## images/
Side, wrist, front (glasses) and relay camera snapshots, plus the live-detection view with the reach map.
The eye-camera close-up was left out on purpose (this repo is public).

## env/
`pip_freeze_lerobot_env.txt`: the conda `lerobot` env (Python 3.12, torch 2.11 cu128, opencv-python 4.13,
ultralytics 8.4, chalkpy). The YOLOE weights (`models/`, ~600 MB) are not in git - they download on first run.
