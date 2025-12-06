# 🎹 Raspberry Pi MIDI Looper

A hardware-based 10-track MIDI looper pedal built with Raspberry Pi 3B+ and designed for use with Casio keyboards. Record, layer, and loop MIDI performances with physical button controls and LED feedback.

## ✨ Features

- **10 Independent Tracks** - Layer multiple MIDI recordings
- **Backing Track Recording** - Hear previous tracks while recording new ones
- **Auto-Looping Playback** - Tracks loop seamlessly based on longest recording
- **Full MIDI Capture** - Records velocity, sustain pedal, and all MIDI CC data
- **Persistent Storage** - Auto-saves session, survives power cycles
- **MIDI Export** - Export as merged or separate MIDI files
- **MIDI Import** - Load existing MIDI files into tracks
- **Visual Feedback** - LED indicators for mode, tracks, and recording status
- **Tone-Independent** - Recordings play in any sound you select on your keyboard

---

## 🛠️ Hardware Requirements

### Components
- **Raspberry Pi 3B+** (1GB RAM)
- **Casio CTX870IN Keyboard** (or compatible USB-MIDI keyboard)
- **4× Tactile Push Buttons**
- **1× Touch Sensor** (TTP223 or similar)
- **10× Green LEDs** (Track indicators)
- **1× Red LED** (Recording mode)
- **1× Yellow LED** (Playing mode)
- **1× Blue LED** (Pause indicator)
- **1× White LED** (Clear track indicator)
- **1× Red LED** (Delete all indicator)
- **Breadboard & Jumper Wires**
- **USB Cable** (USB-A to USB-B for Casio keyboard)
- **Power Supply** (5V 2.5A+ Micro USB)

### Optional
- Resistors (220Ω) for LEDs (code uses PWM at 20% duty cycle as workaround)

---

## 📐 Pin Connections (BCM Numbering)

### Buttons (Pull-Up, connect to GND)
| Component | GPIO Pin | Physical Pin |
|-----------|----------|--------------|
| Button 1 (Mode Toggle) | GPIO 5 | Pin 29 |
| Button 2 (Start/Stop) | GPIO 6 | Pin 31 |
| Button 3 (Left/Pause) | GPIO 13 | Pin 33 |
| Button 4 (Right/Clear) | GPIO 19 | Pin 35 |

### Touch Sensor (Active High)
| Component | GPIO Pin | Physical Pin |
|-----------|----------|--------------|
| Touch Sensor OUT | GPIO 26 | Pin 37 |
| Touch Sensor VCC | 3.3V | Pin 1 or 17 |
| Touch Sensor GND | GND | Any GND pin |

### Status LEDs (Anode to GPIO, Cathode to GND)
| LED Color/Function | GPIO Pin | Physical Pin |
|--------------------|----------|--------------|
| RED (Recording Mode) | GPIO 20 | Pin 38 |
| YELLOW (Playing Mode) | GPIO 21 | Pin 40 |
| BLUE (Pause) | GPIO 22 | Pin 15 |
| WHITE (Clear Track) | GPIO 23 | Pin 16 |
| RED (Delete All) | GPIO 24 | Pin 18 |

### Track LEDs (Green, Anode to GPIO, Cathode to GND)
| Track | GPIO Pin | Physical Pin |
|-------|----------|--------------|
| Track 1 | GPIO 4 | Pin 7 |
| Track 2 | GPIO 17 | Pin 11 |
| Track 3 | GPIO 27 | Pin 13 |
| Track 4 | GPIO 25 | Pin 22 |
| Track 5 | GPIO 12 | Pin 32 |
| Track 6 | GPIO 16 | Pin 36 |
| Track 7 | GPIO 2 | Pin 3 |
| Track 8 | GPIO 3 | Pin 5 |
| Track 9 | GPIO 8 | Pin 24 |
| Track 10 | GPIO 7 | Pin 26 |

### Ground Distribution
Connect all LED cathodes and button grounds to GND pins:
- Pin 6, 9, 14, 20, 25, 30, 34, 39 (all are GND)

---

## 🎛️ Button Functions

### Button 1: Mode Toggle (GPIO 5)
**Recording Mode ↔ Playing Mode**

- Only works when STOPPED (not during recording/playback)
- Visual indicator: RED LED (REC) / YELLOW LED (PLAY)

---

### Button 2: Start/Stop (GPIO 6)
**Master transport control**

#### When Stopped in REC Mode:
- Starts recording on currently selected track
- **Overwrites** previous recording on that track
- Backing tracks (other recorded tracks) loop in background
- RED LED and selected track LED **blink** during recording

#### When Stopped in PLAY Mode:
- Plays all recorded tracks together
- Tracks loop automatically based on longest track duration
- All tracks with recordings light up

#### When Running:
- Stops recording/playback
- Sends MIDI panic (stops stuck notes)
- Auto-saves session (in REC mode)

---

### Button 3: Left/Pause (GPIO 13)
**Context-sensitive dual function**

#### When Stopped in REC Mode:
- Navigate **LEFT** through tracks (10 → 9 → 8 → ... → 1 → 10)
- Selected track LED lights up
- Use to choose which track to record on

#### When Playing in PLAY Mode:
- **PAUSE/RESUME** playback
- BLUE LED lights when paused
- Sends MIDI panic when pausing

---

### Button 4: Right/Clear (GPIO 19)
**Context-sensitive dual function**

#### When Stopped in REC Mode:
- Navigate **RIGHT** through tracks (1 → 2 → 3 → ... → 10 → 1)
- Selected track LED lights up
- Use to choose which track to record on

#### When Playing in PLAY Mode:
- **CLEAR** currently selected track
- WHITE LED flashes briefly
- Track immediately goes silent
- Session auto-saves

---

### Touch Sensor: Delete All (GPIO 26)
**Emergency reset**

- Deletes **ALL 10 tracks** permanently
- Stops playback/recording if running
- Sends MIDI panic
- RED "Delete All" LED flashes for 1 second
- ⚠️ **No undo!**

---

## 💾 Software Features

### Auto-Save System
- **Auto-saves after each recording** in REC mode
- **Auto-saves when tracks are cleared**
- **Auto-loads last session** on startup
- Saves to: `~/looper_autosave/session.json`
- Survives power cycles!

### MIDI Export
Export your compositions via SSH:

```bash
# Type 'save' in the terminal
save

# Choose option:
# 1 - Merge all tracks into one MIDI file
# 2 - Export each track as separate MIDI files

# Files saved to: ~/looper_exports/
```

**Merged export:** `merged_output.mid` (all tracks combined)  
**Separate exports:** `track_1.mid`, `track_2.mid`, etc.

### MIDI Import
Load existing MIDI files into tracks:

```bash
# Syntax: load <track_number> <filepath>
load 1 ~/my_melody.mid
load 5 /home/pi/Downloads/bass_line.mid
```

### Track Status
Check which tracks have recordings:

```bash
status

# Output example:
# Track 1: 245 events, 8.32s
# Track 2: Empty
# Track 3: 189 events, 5.47s
# Max duration: 8.32s
```

---

## 🚀 Installation

### 1. Flash Raspberry Pi OS
- Use **Raspberry Pi OS Lite (64-bit)**
- Enable SSH in imager settings
- Configure WiFi credentials
- Set hostname: `raspberrypi`
- Username: `pi`

### 2. Install Dependencies
```bash
# SSH into Pi
ssh pi@raspberrypi.local

# Update system
sudo apt update
sudo apt upgrade -y

# Install required packages
sudo apt install -y python3-pip python3-rpi.gpio python3-mido python3-rtmidi

# Or if apt doesn't have them:
pip3 install --break-system-packages mido python-rtmidi
```

### 3. Upload Looper Script
```bash
# On your Mac, copy the script to Pi:
scp looper.py pi@raspberrypi.local:~/

# Or create it directly on Pi:
ssh pi@raspberrypi.local
nano looper.py
# Paste the code, save with Ctrl+X, Y, Enter
```

### 4. Test Manually
```bash
# Make sure Casio is plugged in via USB
python3 looper.py

# You should see:
# Found Inputs: ['Casio USB-MIDI...']
# System Ready.

# Press Ctrl+C to stop
```

### 5. Enable Auto-Start (Optional)
```bash
sudo nano /etc/systemd/system/looper.service
```

Paste this:
```ini
[Unit]
Description=MIDI Looper
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi
ExecStart=/usr/bin/python3 /home/pi/looper.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:
```bash
sudo systemctl enable looper.service
sudo systemctl start looper.service

# Check status:
sudo systemctl status looper.service
```

---

## 📁 File Transfer (Mac ↔ Pi)

### From Mac to Pi
```bash
# Copy file to Pi's home directory:
scp /path/to/file.mid pi@raspberrypi.local:~/

# Copy to specific location:
scp melody.mid pi@raspberrypi.local:~/looper_exports/
```

### From Pi to Mac
```bash
# Copy file from Pi to current directory:
scp pi@raspberrypi.local:~/looper_exports/merged_output.mid .

# Copy multiple files:
scp pi@raspberrypi.local:~/looper_exports/*.mid ~/Desktop/
```

### Using SFTP (Alternative)
```bash
# Connect with SFTP:
sftp pi@raspberrypi.local

# SFTP commands:
get looper_exports/merged_output.mid  # Download
put my_track.mid                      # Upload
ls                                    # List files
cd looper_exports                     # Change directory
exit                                  # Quit
```

---

## 🎵 Usage Examples

### Recording Your First Track
1. Power on (default: REC mode, Track 1 selected)
2. Press **Button 2** (Start recording)
   - RED LED and Track 1 LED blink
3. Play your melody on Casio keyboard
4. Press **Button 2** (Stop recording)
   - LEDs stop blinking
   - Track auto-saves

### Layering Additional Tracks
1. Press **Button 4** (Right) to select Track 2
2. Press **Button 2** (Start recording)
   - Track 1 loops in background as you record Track 2
3. Play complementary part
4. Press **Button 2** (Stop)

### Playing Back All Tracks
1. Press **Button 1** to switch to PLAY mode
   - YELLOW LED lights up
   - All recorded tracks' LEDs light up
2. Press **Button 2** (Start playback)
   - All tracks play together, looping continuously
3. Press **Button 3** (Pause) to pause/resume
4. Press **Button 2** (Stop) when done

### Exporting Your Composition
1. SSH into Pi: `ssh pi@raspberrypi.local`
2. Type: `save`
3. Choose: `1` (merged) or `2` (separate tracks)
4. Transfer file to Mac:
   ```bash
   scp pi@raspberrypi.local:~/looper_exports/merged_output.mid ~/Desktop/
   ```

### Loading Existing MIDI
1. Transfer MIDI file to Pi:
   ```bash
   scp bass_line.mid pi@raspberrypi.local:~/
   ```
2. SSH into Pi and load it:
   ```bash
   load 3 ~/bass_line.mid
   ```
3. Track 3 now contains that MIDI data

---

## 🔧 Technical Details

### MIDI Recording
- Captures **all MIDI messages**: note on/off, velocity, sustain, mod wheel, etc.
- Records timing with microsecond precision
- No quantization - records exactly what you play

### Looping Mechanism
- Tracks loop based on **longest track duration**
- Example: Track 1 (5s) + Track 2 (10s) → Track 1 loops twice per cycle
- Seamless loop transitions

### Memory Usage
- Each MIDI event: ~50 bytes
- 1GB RAM can store ~20 million events
- Practical limit: **hours of recording** before RAM fills

### Tone Independence
- Records **MIDI data only**, not audio
- Your keyboard interprets the MIDI
- Change sound on Casio anytime - recording adapts
- Example: Record in Grand Piano, play back as Strings

---

## 🔌 Power Requirements

- **Pi + LEDs:** ~500mA @ 5V (2.5W)
- **Recommended:** 5V 2.5A+ power supply
- **Compatible:** Phone chargers (22.5W+), official Pi adapter, quality power banks

⚠️ **Check for under-voltage:**
```bash
vcgencmd get_throttled
# Output should be: throttled=0x0
```

---

## 🐛 Troubleshooting

### Casio Not Detected
```bash
# Check connected MIDI devices:
python3 -c "import mido; print(mido.get_input_names())"

# Replug USB cable, wait 10 seconds, try again
```

### LEDs Not Working
- Check GPIO connections match pinout
- Verify GND connections
- Test individual LED: `gpio -g mode 20 out; gpio -g write 20 1`

### Under-Voltage Warning
- Use better power supply (5V 2.5A minimum)
- Avoid long/thin USB cables
- Don't use USB hub power

### Service Won't Start
```bash
# Check logs:
sudo journalctl -u looper.service -n 50

# Stop service for manual testing:
sudo systemctl stop looper.service
python3 looper.py
```

### Session Not Auto-Loading
```bash
# Check autosave file exists:
ls -lh ~/looper_autosave/session.json

# If corrupted, delete and start fresh:
rm ~/looper_autosave/session.json
```

---

## 🚦 LED Behavior Reference

| LED | Steady On | Blinking | Flashing |
|-----|-----------|----------|----------|
| RED (Rec) | Recording mode active | Currently recording | - |
| YELLOW (Play) | Playing mode active | - | - |
| BLUE (Pause) | Playback paused | - | - |
| WHITE (Clear) | - | - | Track cleared |
| RED (Delete) | - | - | All tracks deleted |
| Green (Track) | Track selected OR has data | Recording on this track | - |

---

## ⚠️ Important Notes

1. **Recording is overwrite mode** - Re-recording a track erases previous version
2. **Touch sensor deletes everything** - Use carefully!
3. **No resistors = LED risk** - PWM helps, but don't run 24/7
4. **Auto-save is automatic** - Manual save only for MIDI export
5. **Backing tracks loop during recording** - Hear context while recording new parts

---

## 📝 Future Enhancements

- [ ] Overdub mode (layer recordings on same track)
- [ ] Metronome with tempo control
- [ ] Undo last recording
- [ ] Volume control per track
- [ ] OLED display for visual feedback
- [ ] Foot pedal support
- [ ] Quantization options
- [ ] MIDI clock sync

---

## 🤝 Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.

---

## 📄 License

MIT License - Feel free to use and modify for your projects!

---

## 🙏 Acknowledgments

Built with:
- [mido](https://mido.readthedocs.io/) - MIDI library for Python
- [RPi.GPIO](https://pypi.org/project/RPi.GPIO/) - Raspberry Pi GPIO control
- Casio CTX870IN keyboard

---

## 📧 Contact

For questions or suggestions, open an issue on GitHub!

---

**Happy Looping! 🎹🎵**