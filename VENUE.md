# Venue setup checklist

Order matters: each step relies on the ones before it. All commands are run from this folder in a
command prompt (or with the full path, `C:\Users\test\Documents\armproject\<name>.cmd`).
**STOP at any time:** `s` / space in any live window, or `stop.cmd` - the arm freezes where it is
(`home.cmd` parks it afterwards).

## 1. Board and arm (15 min)
1. Lay the board flat; arm base at the middle of the near edge, where it was.
2. Markers, centres in mm from marker 2's centre (x to the right, y away from you), top edge of
   each pointing away: **0 far-left (0, 241.3), 1 far-right (619.6, 241.3), 2 near-left (0, 0),
   3 near-right (619.6, 0)**. Moved them? Edit `scene/config.json`.
3. Power the arm (12 V), USB to the laptop: follower COM4, leader COM7.
4. `home.cmd` - glides to the rest pose and relaxes. Doesn't work? Re-check COM ports.
5. **Arm table calibration** (only if the board or arm moved): hold the arm, `scripted.cmd calibrate`,
   rest the tip on each marker centre (Enter), then the drop spot (`d` + Enter). Fit under 10 mm.

## 2. Side camera (10 min)
1. Corner of the board opposite the arm, ~40 cm up, tilted down ~45 deg, whole board in view.
2. `side_check.cmd`: all four markers boxed green, LOCKED under 10 mm, sharpness at its peak. `q`.
3. Delete `logs\side_table_hold.json` whenever the camera moved (it re-learns from the next frame
   with all four markers).

## 3. Objects (10 min per object)
1. `objects.cmd` (keep it running). Hands off the markers for a second so it locks.
2. For each object - `granola`, `pipe_cleaner`, `inhaler`, `water_bottle` (on its side):
   put it inside the green reach zone, hold the arm, `scripted.cmd teach <name>`, pose the jaws as
   they should grip, Enter, move the arm out of view, wait for `Learned <class>` in the objects window.
   Re-teach whenever the side camera moved.
3. Try: `scripted.cmd pick <name>` at a few spots. `pick_stats.cmd` shows the success rate.

## 4. Glasses (15 min)
1. Laptop Mobile hotspot: same name + password as the iPhone hotspot, 2.4 GHz, on. iPhone hotspot off.
2. Power the glasses; Board B shows up under "Devices connected" (192.168.137.x). Board A on USB.
3. `eye.cmd`: pupil tracked, blinks register (7/7 long, 4/4 double as before).
4. Gaze calibration on the table: `calib.cmd --mode table`.

## 5. Run
- Without the glasses (fallback / testing): `mouse_gaze.cmd` - pointer = gaze, `b` long blink.
- With the glasses: `gaze.cmd --intent --scene http://192.168.137.x/` - look at an object to
  pre-hover, long blink to pick, double blink to cancel.

## If something is off
| symptom | fix |
|---|---|
| picks land 1-2 cm short / low, servos hot | `home.cmd`, wait 10 min (servos sag when hot; picks now park and relax afterwards) |
| object named wrong | re-teach it where it is (`scripted.cmd teach <name>`) |
| OUT OF REACH | move it into the green zone of the reach map (`m` in the objects window) |
| side camera not locked | something covers a marker, or the camera moved: step 2 |
| front camera stalled | Board B: power-cycle; check the hotspot device list |
