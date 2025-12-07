import mido
import time
import threading
import RPi.GPIO as GPIO
import json
from pathlib import Path
from datetime import datetime

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
SUPERSESSION_FILE = AUTOSAVE_DIR / "supersession.json"
NORMAL_SESSION_FILE = AUTOSAVE_DIR / "session.json"

# --- STATE ---
MODE_REC = "REC"
MODE_PLAY = "PLAY"

tracks = [[] for _ in range(10)]
track_durations = [0.0 for _ in range(10)]  # Store actual recorded duration
track_programs = [0 for _ in range(10)]  # Store program/tone for each track
track_channels = [0 for _ in range(10)]  # Store MIDI channel for each track
track_bank_msb = [0 for _ in range(10)]  # Bank Select MSB (CC 0) for 600 tones
track_bank_lsb = [0 for _ in range(10)]  # Bank Select LSB (CC 32) for 600 tones

# CRITICAL: Assign each track to its own MIDI channel for tone isolation
# Track 1 -> Channel 0, Track 2 -> Channel 1, etc.
for i in range(10):
    track_channels[i] = i  # Tracks use channels 0-9

current_track_idx = 0
system_mode = MODE_REC

is_running = False
is_paused = False
is_recording = False
start_time = 0.0
pause_start_time = 0.0
total_pause_duration = 0.0

# Super Looper Mode
super_looper_enabled = False
super_looper_duration = 0.0  # Fixed duration for all tracks in seconds
super_looper_duration_set = False  # Whether duration has been configured

# Track the last program change received (for pre-recording tone setting)
last_program_change = None  # Will store (program, channel) tuple

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

def get_track_duration(track_idx):
    """Get the actual recorded duration of a track"""
    return track_durations[track_idx]

def get_max_track_duration():
    """Get the duration of the longest track (or Super Looper duration if enabled)"""
    if super_looper_enabled and super_looper_duration_set:
        return super_looper_duration
    return max(track_durations)

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
                midi_out.send(mido.Message('control_change', channel=ch, control=120, value=0))
                # Send note offs for all notes
                for note in range(128):
                    midi_out.send(mido.Message('note_off', channel=ch, note=note, velocity=0))
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
    """Auto-save current session as JSON to appropriate file"""
    global super_looper_enabled, super_looper_duration, super_looper_duration_set
    
    try:
        data = {
            'tracks': [],
            'durations': track_durations,
            'programs': track_programs,  # Save tone settings
            'channels': track_channels,   # Save channel settings
            'bank_msb': track_bank_msb,   # Save bank MSB
            'bank_lsb': track_bank_lsb,   # Save bank LSB
            'super_looper_enabled': super_looper_enabled,
            'super_looper_duration': super_looper_duration,
            'super_looper_duration_set': super_looper_duration_set
        }
        
        for track in tracks:
            track_data = []
            for timestamp, msg in track:
                msg_dict = {
                    'time': timestamp,
                    'type': msg.type,
                    'channel': getattr(msg, 'channel', 0)
                }
                
                # Add message-specific attributes
                if hasattr(msg, 'note'):
                    msg_dict['note'] = msg.note
                if hasattr(msg, 'velocity'):
                    msg_dict['velocity'] = msg.velocity
                if hasattr(msg, 'control'):
                    msg_dict['control'] = msg.control
                if hasattr(msg, 'value'):
                    msg_dict['value'] = msg.value
                if hasattr(msg, 'program'):
                    msg_dict['program'] = msg.program
                if hasattr(msg, 'pitch'):
                    msg_dict['pitch'] = msg.pitch
                
                track_data.append(msg_dict)
            data['tracks'].append(track_data)
        
        # Save to appropriate file based on mode
        save_file = SUPERSESSION_FILE if super_looper_enabled else NORMAL_SESSION_FILE
        with open(save_file, 'w') as f:
            json.dump(data, f)
        
        mode_str = "Super Looper" if super_looper_enabled else "Normal"
        print(f"✓ Auto-saved ({mode_str})")
    except Exception as e:
        print(f"Autosave error: {e}")

def autoload_tracks():
    """Load last session from autosave"""
    global tracks, track_durations, track_programs, track_channels
    global track_bank_msb, track_bank_lsb
    global super_looper_enabled, super_looper_duration, super_looper_duration_set
    
    try:
        # Load from appropriate file based on current mode
        save_file = SUPERSESSION_FILE if super_looper_enabled else NORMAL_SESSION_FILE
        
        if not save_file.exists():
            mode_str = "Super Looper" if super_looper_enabled else "Normal"
            print(f"No {mode_str} autosave found")
            return
        
        with open(save_file, 'r') as f:
            data = json.load(f)
        
        # Initialize arrays
        has_saved_durations = 'durations' in data
        if has_saved_durations:
            track_durations = list(data['durations'])  # Make a copy
        else:
            track_durations = [0.0] * 10
        
        # Load program/channel data if available
        if 'programs' in data:
            track_programs[:] = data['programs']
        if 'channels' in data:
            # Override with per-track channels (0-9)
            for i in range(10):
                track_channels[i] = i
        if 'bank_msb' in data:
            track_bank_msb[:] = data['bank_msb']
        if 'bank_lsb' in data:
            track_bank_lsb[:] = data['bank_lsb']
        
        # Restore Super Looper settings
        if 'super_looper_enabled' in data:
            super_looper_enabled = data['super_looper_enabled']
        if 'super_looper_duration' in data:
            super_looper_duration = data['super_looper_duration']
        if 'super_looper_duration_set' in data:
            super_looper_duration_set = data['super_looper_duration_set']
        
        for i, track_data in enumerate(data['tracks']):
            tracks[i] = []
            for event in track_data:
                # Reconstruct MIDI message based on type
                msg_type = event['type']
                channel = event.get('channel', 0)
                
                if msg_type == 'note_on':
                    msg = mido.Message('note_on', 
                                      note=event['note'], 
                                      velocity=event['velocity'],
                                      channel=channel)
                elif msg_type == 'note_off':
                    msg = mido.Message('note_off',
                                      note=event['note'],
                                      velocity=event.get('velocity', 0),
                                      channel=channel)
                elif msg_type == 'control_change':
                    msg = mido.Message('control_change',
                                      control=event['control'],
                                      value=event['value'],
                                      channel=channel)
                elif msg_type == 'program_change':
                    msg = mido.Message('program_change',
                                      program=event['program'],
                                      channel=channel)
                elif msg_type == 'pitchwheel':
                    msg = mido.Message('pitchwheel',
                                      pitch=event['pitch'],
                                      channel=channel)
                else:
                    continue
                
                tracks[i].append((event['time'], msg))
            
            # CRITICAL FIX: If durations weren't saved, calculate from last event
            if not has_saved_durations and len(tracks[i]) > 0:
                track_durations[i] = tracks[i][-1][0]
        
        print("✓ Loaded autosave")
        # Show what was loaded
        for i in range(10):
            if len(tracks[i]) > 0:
                print(f"  Track {i+1}: {len(tracks[i])} events, {track_durations[i]:.2f}s")
    except Exception as e:
        print(f"Autoload error: {e}")

def setup_super_looper_duration():
    """Interactive setup for Super Looper duration"""
    global super_looper_duration, super_looper_duration_set
    
    print("\n=== Super Looper Duration Setup ===")
    print("1. Declare time manually")
    print("2. Get time from first recorded track")
    
    try:
        choice = input("Choose option (1/2): ").strip()
        
        if choice == '1':
            duration_str = input("Enter duration in seconds: ").strip()
            duration = float(duration_str)
            if duration <= 0:
                print("❌ Duration must be positive!")
                return False
            super_looper_duration = duration
            super_looper_duration_set = True
            print(f"✓ Super Looper enabled with {duration:.2f}s fixed duration")
            return True
        
        elif choice == '2':
            super_looper_duration_set = False
            super_looper_duration = 0.0
            print("✓ Super Looper enabled - duration will be set from first recorded track")
            return True
        
        else:
            print("❌ Invalid choice")
            return False
    
    except ValueError:
        print("❌ Invalid number")
        return False
    except KeyboardInterrupt:
        print("\n❌ Cancelled")
        return False

def switch_to_super_looper():
    """Switch from Normal mode to Super Looper mode"""
    global super_looper_enabled
    
    if super_looper_enabled:
        print("⚠️  Already in Super Looper mode!")
        return
    
    # Save current normal session
    print("💾 Saving Normal session...  ")
    autosave_tracks()
    
    # Switch to Super Looper mode
    super_looper_enabled = True
    
    # Load Super Looper session
    print("📂 Loading Super Looper session...")
    autoload_tracks()
    
    # Setup duration if not already configured
    if not super_looper_duration_set and super_looper_duration == 0:
        if not setup_super_looper_duration():
            # Failed setup, revert
            super_looper_enabled = False
            autoload_tracks()
            return
    else:
        print(f"✓ Super Looper mode active (Duration: {super_looper_duration:.2f}s)")
    
    autosave_tracks()

def switch_to_normal_mode():
    """Switch from Super Looper mode to Normal mode"""
    global super_looper_enabled
    
    if not super_looper_enabled:
        print("⚠️  Already in Normal mode!")
        return
    
    # Save current Super Looper session
    print("💾 Saving Super Looper session...")
    autosave_tracks()
    
    # Switch to Normal mode
    super_looper_enabled = False
    
    # Load Normal session
    print("📂 Loading Normal session...")
    autoload_tracks()
    
    print("✓ Switched to Normal Mode")
    autosave_tracks()

def export_midi_merged(filename=None):
    """Export all tracks merged into one MIDI file with timestamp - FIXED TIMING"""
    try:
        if filename is None:
            filename = f"merged_{timestamp}.mid"
        
        # Using standard MIDI parameters
        ticks_per_beat = 480
        tempo = 500000  # 120 BPM in microseconds
        
        mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        
        # Add tempo message at start
        track.append(mido.MetaMessage('set_tempo', tempo=tempo, time=0))
        
        # Collect all events
        all_events = []
        for track_idx, track_data in enumerate(tracks):
            for timestamp, msg in track_data:
                all_events.append((timestamp, msg))
        
        # Sort by time
        all_events.sort(key=lambda x: x[0])
        
        # Convert seconds to MIDI ticks using tempo
        # Formula: ticks = seconds / (tempo_microseconds / 1000000) * ticks_per_beat
        seconds_per_tick = (tempo / 1000000.0) / ticks_per_beat
        
        prev_time = 0
        for timestamp, msg in all_events:
            delta_time = timestamp - prev_time
            delta_ticks = int(delta_time / seconds_per_tick)
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
    """Export each track as separate MIDI file with timestamp - FIXED TIMING"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exported = []
        
        # Using standard MIDI parameters
        ticks_per_beat = 480
        tempo = 500000  # 120 BPM
        seconds_per_tick = (tempo / 1000000.0) / ticks_per_beat
        
        for i, track_data in enumerate(tracks):
            if len(track_data) == 0:
                continue
            
            mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
            track = mido.MidiTrack()
            mid.tracks.append(track)
            
            # Add tempo
            track.append(mido.MetaMessage('set_tempo', tempo=tempo, time=0))
            
            prev_time = 0
            for ts, msg in track_data:
                delta_time = ts - prev_time
                delta_ticks = int(delta_time / seconds_per_tick)
                msg_copy = msg.copy(time=delta_ticks)
                track.append(msg_copy)
                prev_time = ts
            
            filename = f"track_{i+1}_{timestamp}.mid"
            filepath = MIDI_EXPORT_DIR / filename
            mid.save(str(filepath))
            exported.append(str(filepath))
            print(f"✓ Saved: {filepath}")
        
        return exported
    except Exception as e:
        print(f"Export error: {e}")
        return []

def import_midi_to_track(filepath, track_idx):
    """Import MIDI file into specified track - FIXED timing accumulation"""
    try:
        mid = mido.MidiFile(filepath)
        tracks[track_idx] = []
        
        tempo = 500000
        ticks_per_beat = mid.ticks_per_beat
        
        # Collect all events from all tracks with absolute timing
        all_events = []
        
        for midi_track in mid.tracks:
            track_time = 0.0
            
            for msg in midi_track:
                # Convert delta time to seconds
                tick_time = msg.time
                seconds_per_tick = (tempo / 1000000.0) / ticks_per_beat
                delta_seconds = tick_time * seconds_per_tick
                track_time += delta_seconds  # Accumulate time
                
                # Update tempo if needed
                if msg.type == 'set_tempo':
                    tempo = msg.tempo
                    seconds_per_tick = (tempo / 1000000.0) / ticks_per_beat
                
                # Store ALL non-meta messages with absolute time
                if not msg.is_meta:
                    all_events.append((track_time, msg))
                    
                    # Track program changes for tone
                    if msg.type == 'program_change':
                        track_programs[track_idx] = msg.program
                        track_channels[track_idx] = msg.channel
                        print(f"Detected tone: Program {msg.program} on channel {msg.channel}")
        
        # Sort by time and store
        all_events.sort(key=lambda x: x[0])
        tracks[track_idx] = all_events
        
        # Set duration to last event time (or a bit after for note releases)
        if len(tracks[track_idx]) > 0:
            track_durations[track_idx] = tracks[track_idx][-1][0] + 0.5  # Add buffer for final notes
        
        print(f"✓ Imported to Track {track_idx + 1} ({len(tracks[track_idx])} events, {track_durations[track_idx]:.2f}s)")
        autosave_tracks()
    except Exception as e:
        print(f"Import error: {e}")

# --- SEQUENCER - PROPERLY FIXED LOOPING ---
def sequencer_thread():
    """FIXED: Each track loops independently at its own duration"""
    global is_running, is_paused, total_pause_duration, pause_start_time
    
    print("Sequencer Started")
    
    # Get loop duration (longest track OR recording time limit)
    loop_duration = get_max_track_duration()
    
    if loop_duration == 0:
        loop_duration = 999999  # No tracks yet, allow infinite recording
    
    print(f"Master loop duration: {loop_duration:.2f}s")
    
    # Send initial program changes to set tones for each track
    print("\n🎼 Setting up track tones...")
    for i in range(10):
        if len(tracks[i]) > 0:
            channel = track_channels[i]  # Use track's dedicated channel
            
            # Reset pedal/sustain for this channel
            try:
                midi_out.send(mido.Message('control_change', control=64, value=0, channel=channel))
            except:
                pass
            
            if track_programs[i] > 0 or track_bank_msb[i] > 0 or track_bank_lsb[i] > 0:
                try:
                    # Send Bank Select if available (for 600 tones)
                    if track_bank_msb[i] > 0 or track_bank_lsb[i] > 0:
                        # Bank Select MSB (CC 0)
                        midi_out.send(mido.Message('control_change', control=0, value=track_bank_msb[i], channel=channel))
                        # Bank Select LSB (CC 32)
                        midi_out.send(mido.Message('control_change', control=32, value=track_bank_lsb[i], channel=channel))
                        print(f"🎵 Track {i+1} (Ch {channel}): Bank {track_bank_msb[i]}:{track_bank_lsb[i]}, Program {track_programs[i]}")
                    
                    # Send Program Change
                    prog_msg = mido.Message('program_change', program=track_programs[i], channel=channel)
                    midi_out.send(prog_msg)
                    
                    if track_bank_msb[i] == 0 and track_bank_lsb[i] == 0:
                        print(f"🎵 Track {i+1} (Ch {channel}): Program {track_programs[i]}")
                    
                    time.sleep(0.01)  # Small delay between program changes
                except Exception as e:
                    print(f"❌ Error setting tone for Track {i+1}: {e}")
            else:
                print(f"ℹ️  Track {i+1} (Ch {channel}): No tone set (will use default)")
    print()
    
    # Build playlists for each track
    track_playlists = []
    for i, track in enumerate(tracks):
        # In REC mode, skip the track we're recording on
        if system_mode == MODE_REC and i == current_track_idx:
            track_playlists.append([])
        else:
            track_playlists.append(list(track))
    
    # Track which event we're on for each track
    event_indices = [0] * 10
    last_loop_times = [0.0] * 10  # Track when each track last looped
    
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
        
        # Check each track independently with its OWN duration
        for track_idx, playlist in enumerate(track_playlists):
            if len(playlist) == 0:
                continue
            
            # Get THIS track's actual duration
            track_duration = track_durations[track_idx]
            
            if track_duration == 0:
                continue
            
            # Calculate position within THIS track's loop
            track_position = song_time % track_duration
            
            # Check if we've looped (track_position went back to start)
            if track_position < last_loop_times[track_idx]:
                # We looped! Reset event index
                event_indices[track_idx] = 0
                print(f"Track {track_idx + 1} looped at {song_time:.2f}s")
            
            last_loop_times[track_idx] = track_position
            
            # Play all events that should trigger now
            while event_indices[track_idx] < len(playlist):
                evt_time, msg = playlist[event_indices[track_idx]]
                
                if evt_time <= track_position:
                    try:
                        midi_out.send(msg)
                        # Debug program changes
                        if msg.type == 'program_change':
                            print(f"🎵 PLAYBACK: Sent Program Change {msg.program} on Channel {msg.channel} (Track {track_idx + 1})")
                    except Exception as e:
                        print(f"MIDI send error: {e}")
                    event_indices[track_idx] += 1
                else:
                    break
        
        time.sleep(0.001)
    
    total_pause_duration = local_pause_duration
    midi_panic()
    print("Sequencer Stopped")

def midi_recorder():
    """Record MIDI input - ALL message types, remap to track's channel"""
    global last_program_change
    
    print(f"Listening on {midi_in.name}...")
    print("🎵 Waiting for MIDI input (including Program Changes for tone)...")
    
    for msg in midi_in:
        # Track ALL program changes and bank selects (even when not recording)
        if msg.type == 'program_change':
            last_program_change = (msg.program, msg.channel)
            print(f"🎹 INCOMING Program Change: Program #{msg.program} on Channel {msg.channel}")
            print(f"   (Will be used for next recording)")
        
        # Track bank select messages for 600-tone support
        if msg.type == 'control_change':
            if msg.control == 0:  # Bank Select MSB
                print(f"🏦 INCOMING Bank Select MSB: {msg.value} on Channel {msg.channel}")
            elif msg.control == 32:  # Bank Select LSB
                print(f"🏦 INCOMING Bank Select LSB: {msg.value} on Channel {msg.channel}")
            elif msg.control == 64:  # Sustain pedal
                pedal_state = "ON" if msg.value >= 64 else "OFF"
        
        # Only record when actively recording
        if is_running and not is_paused and system_mode == MODE_REC and is_recording:
            now = time.perf_counter()
            rec_time = now - start_time - total_pause_duration
            
            if rec_time >= 0:
                # CRITICAL: Remap message to track's dedicated channel
                track_channel = track_channels[current_track_idx]
                
                if hasattr(msg, 'channel'):
                    # Create new message with track's channel
                    msg_remapped = msg.copy(channel=track_channel)
                else:
                    msg_remapped = msg
                
                tracks[current_track_idx].append((rec_time, msg_remapped))
                
                # Track program changes
                if msg.type == 'program_change':
                    track_programs[current_track_idx] = msg.program
                    print(f"✅ RECORDED Program Change: Program #{msg.program} → Track {current_track_idx + 1} (Ch {track_channel})")
                
                # Track bank selects
                if msg.type == 'control_change':
                    if msg.control == 0:  # Bank MSB
                        track_bank_msb[current_track_idx] = msg.value
                        print(f"✅ RECORDED Bank MSB: {msg.value} → Track {current_track_idx + 1}")
                    elif msg.control == 32:  # Bank LSB
                        track_bank_lsb[current_track_idx] = msg.value
                        print(f"✅ RECORDED Bank LSB: {msg.value} → Track {current_track_idx + 1}")

# --- BUTTON LOGIC ---
def handle_buttons():
    global system_mode, current_track_idx, is_running, is_paused, is_recording
    global start_time, total_pause_duration, tracks, track_durations
    global last_program_change
    global super_looper_enabled, super_looper_duration, super_looper_duration_set
    
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
                    # STOPPING
                    stop_time = time.perf_counter()
                    
                    # If we were recording, set the actual duration
                    if is_recording:
                        actual_duration = stop_time - start_time - total_pause_duration
                        
                        # Super Looper duration enforcement
                        if super_looper_enabled:
                            # If this is the first track in "from first track" mode, set the duration
                            if not super_looper_duration_set:
                                super_looper_duration = actual_duration
                                super_looper_duration_set = True
                                print(f"✓ Super Looper duration set to {actual_duration:.2f}s from Track {current_track_idx + 1}")
                            
                            # Force track duration to Super Looper duration
                            track_durations[current_track_idx] = super_looper_duration
                            
                            if actual_duration < super_looper_duration:
                                print(f"Recorded {actual_duration:.2f}s on Track {current_track_idx + 1}")
                                print(f"  Track will loop to fill {super_looper_duration:.2f}s")
                            elif actual_duration > super_looper_duration:
                                print(f"Recorded {actual_duration:.2f}s on Track {current_track_idx + 1}")
                                print(f"  ⚠️  Exceeded Super Looper duration by {actual_duration - super_looper_duration:.2f}s")
                            else:
                                print(f"Recorded {actual_duration:.2f}s on Track {current_track_idx + 1} (perfect fit!)")
                        else:
                            # Normal mode
                            track_durations[current_track_idx] = actual_duration
                            print(f"Recorded {actual_duration:.2f}s on Track {current_track_idx + 1}")
                    
                    is_running = False
                    is_paused = False
                    is_recording = False
                    midi_panic()
                    print("Stopped")
                    
                    # Auto-save after recording
                    if system_mode == MODE_REC:
                        autosave_tracks()
                else:
                    # STARTING
                    if system_mode == MODE_REC:
                        tracks[current_track_idx] = []
                        track_durations[current_track_idx] = 0.0
                        
                        # Inject last program change at the start if available
                        if last_program_change is not None:
                            program, _ = last_program_change  # Ignore input channel
                            track_channel = track_channels[current_track_idx]  # Use track's channel
                            track_programs[current_track_idx] = program
                            
                            # Create program change message with track's channel
                            prog_msg = mido.Message('program_change', program=program, channel=track_channel)
                            tracks[current_track_idx].append((0.0, prog_msg))
                            
                            print(f"📌 Using pre-recording tone: Program {program}")
                            print(f"   ✅ Injected at start of Track {current_track_idx + 1} (Channel {track_channel})")
                        
                        is_recording = True
                        if super_looper_enabled and super_looper_duration_set:
                            print(f"Recording Track {current_track_idx + 1} (Max: {super_looper_duration:.2f}s)...")
                        else:
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
                    track_durations[current_track_idx] = 0.0
                    flash_led(LED_CLEAR)
                    print(f"Track {current_track_idx + 1} Cleared!")
                    autosave_tracks()
            
            # DELETE ALL
            if s_touch == 1 and last_states[TOUCH_SENSOR] == 0:
                is_running = False
                is_paused = False
                is_recording = False
                midi_panic()
                # CRITICAL FIX: Properly clear all track data with global scope
                for i in range(10):
                    tracks[i] = []
                    track_durations[i] = 0.0
                    track_programs[i] = 0
                    track_channels[i] = i  # Reset to default channel
                    track_bank_msb[i] = 0
                    track_bank_lsb[i] = 0
                
                # Reset Super Looper duration if in Super Looper mode
                if super_looper_enabled:
                    super_looper_duration = 0.0
                    super_looper_duration_set = False
                    flash_led(LED_DELETE_ALL, duration=1.0)
                    print("--- ALL TRACKS DELETED ---")
                    print("🔓 Super Looper duration reset")
                    autosave_tracks()
                    
                    # Immediately prompt for new duration setup
                    setup_super_looper_duration()
                    autosave_tracks()
                else:
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
    print("SL ON - Enable Super Looper mode")
    print("SL OFF - Disable Super Looper mode")
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
                parts = cmd.split(maxsplit=2)
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
                if super_looper_enabled:
                    if super_looper_duration_set:
                        print(f"Mode: 🔒 SUPER LOOPER (Fixed Duration: {super_looper_duration:.2f}s)")
                    else:
                        print(f"Mode: 🔒 SUPER LOOPER (Duration from first track)")
                else:
                    print("Mode: Normal")
                print()
                for i, track in enumerate(tracks):
                    if len(track) > 0:
                        duration = track_durations[i]
                        forced = " (forced to SL duration)" if super_looper_enabled and super_looper_duration_set else ""
                        print(f"Track {i+1}: {len(track)} events, {duration:.2f}s{forced}")
                    else:
                        print(f"Track {i+1}: Empty")
                if super_looper_enabled and super_looper_duration_set:
                    print(f"\nAll tracks loop to: {super_looper_duration:.2f}s")
                else:
                    max_dur = get_max_track_duration()
                    print(f"Longest track: {max_dur:.2f}s")
                print("====================\n")
            
            elif cmd.lower() == 'sl on':
                switch_to_super_looper()
                update_ui()
            
            elif cmd.lower() == 'sl off':
                switch_to_normal_mode()
                update_ui()
            
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