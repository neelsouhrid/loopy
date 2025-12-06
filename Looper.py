import mido
import time
import threading
import RPi.GPIO as GPIO
import os
import json
from pathlib import Path

# --- CONFIGURATION ---
BTN_MODE = 5
BTN_ACTION = 6
BTN_L_PAUSE = 13
BTN_R_CLEAR = 19
TOUCH_SENSOR = 26

LED_REC_MODE = 20
LED_PLAY_MODE = 21
LED_PAUSE = 22
LED_CLEAR = 23
LED_DELETE_ALL = 24

TRACK_LEDS = [4, 17, 27, 25, 12, 16, 2, 3, 8, 7]

# Storage paths
AUTOSAVE_DIR = Path.home() / "looper_autosave"
MIDI_EXPORT_DIR = Path.home() / "looper_exports"

# --- STATE ---
MODE_REC = "REC"
MODE_PLAY = "PLAY"

tracks = [[] for _ in range(10)]
current_track_idx = 0
system_mode = MODE_REC

is_running = False
is_paused = False
is_recording = False
start_time = 0.0
pause_start_time = 0.0
total_pause_duration = 0.0
max_track_duration = 0.0  # For looping sync

midi_in = None
midi_out = None
pwm_leds = {}
blink_states = {}
blink_thread_running = False

# --- SETUP ---
def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in [BTN_MODE, BTN_ACTION, BTN_L_PAUSE, BTN_R_CLEAR]:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    GPIO.setup(TOUCH_SENSOR, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    all_leds = [LED_REC_MODE, LED_PLAY_MODE, LED_PAUSE, LED_CLEAR, LED_DELETE_ALL] + TRACK_LEDS
    for pin in all_leds:
        GPIO.setup(pin, GPIO.OUT)
        pwm = GPIO.PWM(pin, 120)
        pwm.start(0)
        pwm_leds[pin] = pwm
        blink_states[pin] = False

def set_led(pin, state):
    duty = 20 if state else 0
    pwm_leds[pin].ChangeDutyCycle(duty)

def flash_led(pin, duration=0.2):
    set_led(pin, True)
    time.sleep(duration)
    set_led(pin, False)

def update_ui():
    """Update all LEDs based on current state"""
    # Mode LEDs
    if is_recording:
        # Recording LED blinks in blink thread
        pass
    else:
        set_led(LED_REC_MODE, system_mode == MODE_REC)
    
    set_led(LED_PLAY_MODE, system_mode == MODE_PLAY)
    set_led(LED_PAUSE, is_paused)
    
    # Track LEDs
    for i, pin in enumerate(TRACK_LEDS):
        if system_mode == MODE_PLAY:
            # Show tracks with recordings
            set_led(pin, len(tracks[i]) > 0)
        else:
            # Show selected track (will blink if recording)
            if i == current_track_idx and is_recording:
                # Blink thread handles this
                pass
            else:
                set_led(pin, i == current_track_idx)

def blink_thread_func():
    """Separate thread for blinking LEDs during recording"""
    global blink_thread_running
    blink_thread_running = True
    
    while blink_thread_running:
        if is_recording:
            # Blink recording LED and selected track LED
            state = blink_states[LED_REC_MODE]
            blink_states[LED_REC_MODE] = not state
            set_led(LED_REC_MODE, state)
            set_led(TRACK_LEDS[current_track_idx], state)
            time.sleep(0.5)
        else:
            time.sleep(0.1)

# --- MIDI ---
def midi_panic():
    if midi_out:
        try:
            for ch in range(16):
                midi_out.send(mido.Message('control_change', channel=ch, control=123, value=0))
                midi_out.send(mido.Message('control_change', channel=ch, control=121, value=0))
        except Exception as e:
            print(f"MIDI panic error: {e}")

def get_midi_ports():
    inputs = mido.get_input_names()
    outputs = mido.get_output_names()
    print("Found Inputs:", inputs)
    print("Found Outputs:", outputs)
    
    in_port = next((n for n in inputs if "MIDI" in n or "Casio" in n), None)
    out_port = next((n for n in outputs if "MIDI" in n or "Casio" in n), None)
    return in_port, out_port

# --- STORAGE ---
def ensure_directories():
    AUTOSAVE_DIR.mkdir(exist_ok=True)
    MIDI_EXPORT_DIR.mkdir(exist_ok=True)

def autosave_tracks():
    """Auto-save current session as JSON"""
    try:
        data = {
            'tracks': [],
            'max_duration': max_track_duration
        }
        
        for track in tracks:
            track_data = []
            for timestamp, msg in track:
                track_data.append({
                    'time': timestamp,
                    'type': msg.type,
                    'note': getattr(msg, 'note', None),
                    'velocity': getattr(msg, 'velocity', None),
                    'control': getattr(msg, 'control', None),
                    'value': getattr(msg, 'value', None),
                    'channel': getattr(msg, 'channel', 0)
                })
            data['tracks'].append(track_data)
        
        with open(AUTOSAVE_DIR / 'session.json', 'w') as f:
            json.dump(data, f)
        
        print("✓ Auto-saved")
    except Exception as e:
        print(f"Autosave error: {e}")

def autoload_tracks():
    """Load last session from autosave"""
    global tracks, max_track_duration
    
    try:
        save_file = AUTOSAVE_DIR / 'session.json'
        if not save_file.exists():
            print("No autosave found")
            return
        
        with open(save_file, 'r') as f:
            data = json.load(f)
        
        max_track_duration = data.get('max_duration', 0.0)
        
        for i, track_data in enumerate(data['tracks']):
            tracks[i] = []
            for event in track_data:
                # Reconstruct MIDI message
                if event['type'] == 'note_on':
                    msg = mido.Message('note_on', 
                                      note=event['note'], 
                                      velocity=event['velocity'],
                                      channel=event['channel'])
                elif event['type'] == 'note_off':
                    msg = mido.Message('note_off',
                                      note=event['note'],
                                      velocity=event['velocity'],
                                      channel=event['channel'])
                elif event['type'] == 'control_change':
                    msg = mido.Message('control_change',
                                      control=event['control'],
                                      value=event['value'],
                                      channel=event['channel'])
                else:
                    continue
                
                tracks[i].append((event['time'], msg))
        
        print("✓ Loaded autosave")
    except Exception as e:
        print(f"Autoload error: {e}")

def export_midi_merged(filename="merged_output.mid"):
    """Export all tracks merged into one MIDI file"""
    try:
        mid = mido.MidiFile()
        track = mido.MidiTrack()
        mid.tracks.append(track)
        
        # Collect all events
        all_events = []
        for track_data in tracks:
            for timestamp, msg in track_data:
                all_events.append((timestamp, msg))
        
        # Sort by time
        all_events.sort(key=lambda x: x[0])
        
        # Convert to MIDI with delta times
        prev_time = 0
        for timestamp, msg in all_events:
            delta_ticks = int((timestamp - prev_time) * 480)  # 480 ticks per beat
            msg_copy = msg.copy(time=delta_ticks)
            track.append(msg_copy)
            prev_time = timestamp
        
        filepath = MIDI_EXPORT_DIR / filename
        mid.save(str(filepath))
        print(f"✓ Saved: {filepath}")
        return str(filepath)
    except Exception as e:
        print(f"Export error: {e}")
        return None

def export_midi_separate():
    """Export each track as separate MIDI file"""
    try:
        exported = []
        for i, track_data in enumerate(tracks):
            if len(track_data) == 0:
                continue
            
            mid = mido.MidiFile()
            track = mido.MidiTrack()
            mid.tracks.append(track)
            
            prev_time = 0
            for timestamp, msg in track_data:
                delta_ticks = int((timestamp - prev_time) * 480)
                msg_copy = msg.copy(time=delta_ticks)
                track.append(msg_copy)
                prev_time = timestamp
            
            filename = f"track_{i+1}.mid"
            filepath = MIDI_EXPORT_DIR / filename
            mid.save(str(filepath))
            exported.append(str(filepath))
            print(f"✓ Saved: {filepath}")
        
        return exported
    except Exception as e:
        print(f"Export error: {e}")
        return []

def import_midi_to_track(filepath, track_idx):
    """Import MIDI file into specified track"""
    global max_track_duration
    
    try:
        mid = mido.MidiFile(filepath)
        tracks[track_idx] = []
        
        current_time = 0.0
        for msg in mid:
            if not msg.is_meta:
                current_time += msg.time / 480.0  # Convert ticks to seconds
                tracks[track_idx].append((current_time, msg))
        
        # Update max duration
        if len(tracks[track_idx]) > 0:
            track_duration = tracks[track_idx][-1][0]
            max_track_duration = max(max_track_duration, track_duration)
        
        print(f"✓ Imported to Track {track_idx + 1}")
        autosave_tracks()
    except Exception as e:
        print(f"Import error: {e}")

# --- SEQUENCER ---
def sequencer_thread():
    global is_running, is_paused, total_pause_duration, pause_start_time, max_track_duration
    
    print("Sequencer Started")
    
    # Build playlist
    playlist = []
    for i, track in enumerate(tracks):
        if system_mode == MODE_REC and i == current_track_idx:
            continue
        for event in track:
            playlist.append(event)
    
    playlist.sort(key=lambda x: x[0])
    
    # Calculate loop duration
    loop_duration = max_track_duration if max_track_duration > 0 else 999999
    
    event_idx = 0
    base_time = start_time
    local_pause_duration = 0
    local_pause_start = 0
    
    while is_running:
        if is_paused:
            if local_pause_start == 0:
                local_pause_start = time.perf_counter()
                midi_panic()
            time.sleep(0.05)
            continue
        
        if local_pause_start != 0:
            local_pause_duration += (time.perf_counter() - local_pause_start)
            local_pause_start = 0
        
        now = time.perf_counter()
        song_time = now - base_time - local_pause_duration
        
        # Loop if in PLAY mode
        if system_mode == MODE_PLAY and song_time >= loop_duration:
            # Reset for loop
            song_time = song_time % loop_duration
            base_time = now - song_time - local_pause_duration
            event_idx = 0
        
        # Trigger events
        while event_idx < len(playlist):
            evt_time, msg = playlist[event_idx]
            
            # Adjust for looping
            target_time = evt_time
            if system_mode == MODE_PLAY and loop_duration < 999999:
                target_time = evt_time % loop_duration
            
            if song_time >= target_time:
                try:
                    midi_out.send(msg)
                except Exception as e:
                    print(f"MIDI send error: {e}")
                event_idx += 1
            else:
                break
        
        time.sleep(0.001)
    
    total_pause_duration = local_pause_duration
    print("Sequencer Stopped")

def midi_recorder():
    global max_track_duration
    
    print(f"Listening on {midi_in.name}...")
    
    for msg in midi_in:
        if is_running and not is_paused and system_mode == MODE_REC and is_recording:
            now = time.perf_counter()
            rec_time = now - start_time - total_pause_duration
            
            if rec_time >= 0:
                tracks[current_track_idx].append((rec_time, msg))
                # Update max duration
                max_track_duration = max(max_track_duration, rec_time)

# --- BUTTON LOGIC ---
def handle_buttons():
    global system_mode, current_track_idx, is_running, is_paused, is_recording
    global start_time, total_pause_duration, tracks, max_track_duration
    
    last_states = {
        BTN_MODE: 1,
        BTN_ACTION: 1,
        BTN_L_PAUSE: 1,
        BTN_R_CLEAR: 1,
        TOUCH_SENSOR: 0
    }
    
    try:
        while True:
            s_mode = GPIO.input(BTN_MODE)
            s_action = GPIO.input(BTN_ACTION)
            s_left = GPIO.input(BTN_L_PAUSE)
            s_right = GPIO.input(BTN_R_CLEAR)
            s_touch = GPIO.input(TOUCH_SENSOR)
            
            # MODE TOGGLE
            if s_mode == 0 and last_states[BTN_MODE] == 1:
                if not is_running:
                    system_mode = MODE_PLAY if system_mode == MODE_REC else MODE_REC
                    print(f"Mode: {system_mode}")
                    update_ui()
                else:
                    print("Stop first!")
            
            # START/STOP
            if s_action == 0 and last_states[BTN_ACTION] == 1:
                if is_running:
                    is_running = False
                    is_paused = False
                    is_recording = False
                    midi_panic()
                    print("Stopped")
                    
                    # Auto-save after recording
                    if system_mode == MODE_REC:
                        autosave_tracks()
                else:
                    if system_mode == MODE_REC:
                        tracks[current_track_idx] = []
                        is_recording = True
                        print(f"Recording Track {current_track_idx + 1}...")
                    else:
                        print("Playing...")
                    
                    is_running = True
                    is_paused = False
                    start_time = time.perf_counter()
                    total_pause_duration = 0
                    
                    t_seq = threading.Thread(target=sequencer_thread, daemon=True)
                    t_seq.start()
                
                update_ui()
            
            # LEFT/PAUSE
            if s_left == 0 and last_states[BTN_L_PAUSE] == 1:
                if not is_running:
                    current_track_idx = (current_track_idx - 1) % 10
                    print(f"Track {current_track_idx + 1}")
                    update_ui()
                else:
                    is_paused = not is_paused
                    print("Paused" if is_paused else "Resuming...")
                    update_ui()
            
            # RIGHT/CLEAR
            if s_right == 0 and last_states[BTN_R_CLEAR] == 1:
                if not is_running:
                    current_track_idx = (current_track_idx + 1) % 10
                    print(f"Track {current_track_idx + 1}")
                    update_ui()
                elif system_mode == MODE_PLAY:
                    tracks[current_track_idx] = []
                    flash_led(LED_CLEAR)
                    print(f"Track {current_track_idx + 1} Cleared!")
                    autosave_tracks()
            
            # DELETE ALL
            if s_touch == 1 and last_states[TOUCH_SENSOR] == 0:
                is_running = False
                is_paused = False
                is_recording = False
                midi_panic()
                tracks = [[] for _ in range(10)]
                max_track_duration = 0.0
                flash_led(LED_DELETE_ALL, duration=1.0)
                print("--- ALL TRACKS DELETED ---")
                autosave_tracks()
                update_ui()
            
            last_states[BTN_MODE] = s_mode
            last_states[BTN_ACTION] = s_action
            last_states[BTN_L_PAUSE] = s_left
            last_states[BTN_R_CLEAR] = s_right
            last_states[TOUCH_SENSOR] = s_touch
            
            time.sleep(0.05)
    
    except KeyboardInterrupt:
        print("Shutdown...")

# --- CLI COMMANDS ---
def cli_thread():
    """Background thread for CLI commands"""
    print("\n=== CLI Commands Available ===")
    print("save - Export MIDI files")
    print("load <track> <file.mid> - Import MIDI to track")
    print("status - Show track info")
    print("==============================\n")
    
    while True:
        try:
            cmd = input().strip().lower()
            
            if cmd == 'save':
                print("\n1. Merge all tracks into one file")
                print("2. Export tracks separately")
                choice = input("Choose (1/2): ").strip()
                
                if choice == '1':
                    export_midi_merged()
                elif choice == '2':
                    export_midi_separate()
                else:
                    print("Invalid choice")
            
            elif cmd.startswith('load'):
                parts = cmd.split()
                if len(parts) == 3:
                    track_num = int(parts[1]) - 1
                    filepath = parts[2]
                    if 0 <= track_num < 10:
                        import_midi_to_track(filepath, track_num)
                    else:
                        print("Track must be 1-10")
                else:
                    print("Usage: load <track_number> <filepath>")
            
            elif cmd == 'status':
                print("\n=== Track Status ===")
                for i, track in enumerate(tracks):
                    if len(track) > 0:
                        duration = track[-1][0]
                        print(f"Track {i+1}: {len(track)} events, {duration:.2f}s")
                    else:
                        print(f"Track {i+1}: Empty")
                print(f"Max duration: {max_track_duration:.2f}s")
                print("====================\n")
            
        except Exception as e:
            print(f"Error: {e}")

# --- MAIN ---
if __name__ == "__main__":
    setup_gpio()
    ensure_directories()
    autoload_tracks()
    update_ui()
    
    try:
        in_port, out_port = get_midi_ports()
        
        if in_port and out_port:
            midi_in = mido.open_input(in_port)
            midi_out = mido.open_output(out_port)
            
            # Start threads
            t_rec = threading.Thread(target=midi_recorder, daemon=True)
            t_rec.start()
            
            t_blink = threading.Thread(target=blink_thread_func, daemon=True)
            t_blink.start()
            
            t_cli = threading.Thread(target=cli_thread, daemon=True)
            t_cli.start()
            
            print("System Ready.")
            for _ in range(3):
                set_led(LED_REC_MODE, True)
                time.sleep(0.1)
                set_led(LED_REC_MODE, False)
                time.sleep(0.1)
            update_ui()
            
            handle_buttons()
        else:
            print("ERROR: Casio USB MIDI not found.")
            while True:
                flash_led(LED_DELETE_ALL, 0.1)
                time.sleep(0.1)
    
    except Exception as e:
        print(f"Critical Error: {e}")
    finally:
        blink_thread_running = False
        midi_panic()
        GPIO.cleanup()
        if midi_in:
            midi_in.close()
        if midi_out:
            midi_out.close()