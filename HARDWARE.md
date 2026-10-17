# armtracking

Gaze-controlled SO-101 robot arm: look at an object on the table, long-blink to confirm, the arm picks it up.

- **Eye camera** (XIAO ESP32S3 Sense, OpenIris USB/UVC firmware, IR LED) → pupil position
- **Front camera** on the glasses (XIAO, OpenIris Wi-Fi) → sees the table and its 4 ArUco markers
- **Gaze calibration** maps pupil → front-camera pixel; the markers map that to table millimetres every frame, so head movement doesn't matter
- **Fixation + long blink** selects an object → the **SO-101 arm** (LeRobot, ACT policy per object) picks it

## Setup (Windows)

1. Miniconda env with Python 3.12 + ffmpeg:
   `conda create -n lerobot python=3.12 ffmpeg -c conda-forge --override-channels`
2. LeRobot (cloned next to this repo's files, not committed):
   `git clone https://github.com/huggingface/lerobot.git` then `pip install -e "./lerobot[feetech]"`
3. Use the GUI build of OpenCV (LeRobot installs the headless one):
   `pip uninstall -y opencv-python-headless` then `pip install opencv-python==4.13.0.92`
4. GPU PyTorch for training: `pip install --force-reinstall --no-deps torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128`

The `.cmd` launchers call the env's Python directly, so no `conda activate` is needed.

## Launchers

| Command | What it does |
|---|---|
| `eye.cmd --camera 3` | Live pupil view: eye box (`r`), save (`w`), spread test (`s`), blink labels |
| `calib.cmd --marker 0.4` | Gaze calibration with on-screen targets → `gaze/calib.json` |
| `gaze.cmd` | Live gaze on the front camera: table mm, dwell ring, object selection, blink commands |
| `front.cmd --camera http://172.20.10.2/` | Front camera + table markers + table mm under the mouse |
| `cameras.cmd` | Arm front/wrist camera setup and "has it moved" check → `arm/cameras.json` |
| `calibrate_arm.cmd follower` / `leader` | Recalibrate an arm |
| `teleop.cmd` | Leader arm drives the follower |
| `record.cmd <name> "<task>"` | Record demonstrations (one dataset per object) |
| `train.cmd <name>` | Train an ACT policy (4 GB GPU sized) |
| `eval.cmd <name>` | Test a policy on the arm and log the success rate |

## Layout

- `eye/` pupil detector, blink/validity logic, live viewer
- `gaze/` capture threads, calibration fit, fixations, object selection, live gaze view
- `scene/` table ArUco markers + homography, printable marker sheet (`python -m scene.make_markers`)
- `arm/` camera config/check tool, record / train / evaluate wrappers
- `check_arm.py`, `wave_arm.py`, `view_cams.py` quick hardware checks

Hardware ports used here: follower COM4, leader COM7, eye camera = DirectShow camera 3, front camera Board B at `http://172.20.10.2/` on the phone hotspot.

## Sharing the cameras (YOLO on another machine)

`relay.cmd` opens each camera once on this laptop and re-serves it to any number of viewers
(Board B allows only one viewer; USB cameras only one program):

| URL (laptop IP on the hotspot, e.g. 172.20.10.4) | Camera |
|---|---|
| `http://<ip>:8080/` | index page, all streams |
| `http://<ip>:8080/side` | arm side camera (rotation from `arm/cameras.json`) |
| `http://<ip>:8080/wrist` | arm wrist camera |
| `http://<ip>:8080/eye` | glasses eye camera |
| `http://<ip>:8080/front` | glasses front camera (un-mirrored) |
| `http://<ip>:8080/<name>.jpg` | latest single frame |

On the MacBook: `pip install ultralytics opencv-python`, then `python streams/mac_yolo.py --host <ip>`
(or any `cv2.VideoCapture(url)` / `YOLO(...).predict(url, stream=True)`).
Our own tools can read the relay too: `gaze.cmd --eye http://localhost:8080/eye --scene http://localhost:8080/front`.

Both machines must be on the iPhone hotspot (Harvard Wi-Fi blocks device-to-device traffic), and Windows
must allow Python through its firewall the first time. Stop the relay (or use `--only eye front`) while
LeRobot records, evaluates or teleoperates with cameras - it needs the arm cameras itself.
