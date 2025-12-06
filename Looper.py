import mido
import time
import threading
import RPi.GPIO as GPIO

# --- CONFIGURATION ---
# GPIO Pins (BCM Mode)
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

# Track LEDs (Indices 0-9 corresponding to Tracks 1-10)
TRACK_LEDS = [4, 17, 27, 25, 12, 16, 2, 3, 8, 7]

# --- STATE CONSTANTS ---
MODE_REC = "REC"
MODE_PLAY = "PLAY"

# --- GLOBAL STATE ---
tracks = [[] for _ in range(10)]
current_track_idx = 0
system_mode = MODE_REC

# Transport State
is_running = False
is_paused = False
start_time = 0.0
pause_start_time = 0.0
total_pause_duration = 0.0

midi_in = None
midi_out = None
pwm_leds = {}
sequencer_lock = threading.Lock()  # Prevent multiple sequencer threads

# --- HARDWARE HELPERS ---
def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # Inputs (Buttons connect to GND -> Pull Up)
    inputs_btns = [BTN_MODE, BTN_ACTION, BTN_L_PAUSE, BTN_R_CLEAR]
    for pin in inputs_btns:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Touch Sensor (Assuming Active High like TTP223)
    GPIO.setup(TOUCH_SENSOR, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    # Outputs (LEDs)
    all_leds = [LED_REC_MODE, LED_PLAY_MODE, LED_PAUSE, LED_CLEAR, LED_DELETE_ALL] + TRACK_LEDS
    for pin in all_leds:
        GPIO.setup(pin, GPIO.OUT)
        # SAFETY: Soft PWM at 20% duty cycle
        pwm = GPIO.PWM(pin, 120) 
        pwm.start(0) 
        pwm_leds[pin] = pwm

def set_led(pin, state):
    duty = 20 if state else 0
    pwm_leds[pin].ChangeDutyCycle(duty)

def flash_led(pin, duration=0.2):
    set_led(pin, True)
    time.sleep(duration)
    set_led(pin, False)

def update_ui():
    set_led(LED_REC_MODE, system_mode == MODE_REC)
    set_led(LED_PLAY_MODE, system_mode == MODE_PLAY)
    set_led(LED_PAUSE, is_paused)
    
    for i, pin in enumerate(TRACK_LEDS):
        set_led(pin, i == current_track_idx)

# --- MIDI HELPERS ---
def midi_panic():
    """Send All Notes Off and Reset Controllers to all channels."""
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

# --- CORE ENGINE ---
def sequencer_thread():
    """Handles Playback AND Recording timing."""
    global is_running, is_paused, total_pause_duration, pause_start_time
    
    print("Sequencer Started")
    
    # Prepare playlist
    playlist = []
    for i, track in enumerate(tracks):
        # In REC mode, skip current track (backing tracks only)
        if system_mode == MODE_REC and i == current_track_idx:
            continue
        for event in track:
            playlist.append(event)
            
    playlist.sort(key=lambda x: x[0])
    
    event_idx = 0
    total_events = len(playlist)
    
    # Use the global start_time as reference
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
        
        # Resume from pause
        if local_pause_start != 0:
            local_pause_duration += (time.perf_counter() - local_pause_start)
            local_pause_start = 0
            
        # Current song time
        now = time.perf_counter()
        song_time = now - base_time - local_pause_duration
        
        # Trigger events
        while event_idx < total_events:
            evt_time, msg = playlist[event_idx]
            
            if song_time >= evt_time:
                try:
                    midi_out.send(msg)
                except Exception as e:
                    print(f"MIDI send error: {e}")
                event_idx += 1
            else:
                break
        
        time.sleep(0.001)
    
    # Update global pause duration for recorder thread
    total_pause_duration = local_pause_duration
    print("Sequencer Stopped")


def midi_recorder():
    """Listens for MIDI input and records when active."""
    print(f"Listening on {midi_in.name}...")
    
    for msg in midi_in:
        # Optional: Pass-through if Casio has Local Control OFF
        # midi_out.send(msg)
        
        if is_running and not is_paused and system_mode == MODE_REC:
            now = time.perf_counter()
            rec_time = now - start_time - total_pause_duration
            
            if rec_time >= 0:
                tracks[current_track_idx].append((rec_time, msg))

# --- BUTTON LOGIC ---
def handle_buttons():
    global system_mode, current_track_idx, is_running, is_paused
    global start_time, total_pause_duration, tracks
    
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
            
            # 1. MODE TOGGLE (only when stopped)
            if s_mode == 0 and last_states[BTN_MODE] == 1:
                if not is_running:
                    system_mode = MODE_PLAY if system_mode == MODE_REC else MODE_REC
                    print(f"Mode: {system_mode}")
                    update_ui()
                else:
                    print("Stop first!")

            # 2. START / STOP
            if s_action == 0 and last_states[BTN_ACTION] == 1:
                if is_running:
                    is_running = False
                    is_paused = False
                    midi_panic()
                    print("Stopped")
                else:
                    # Prevent multiple sequencer threads
                    if sequencer_lock.acquire(blocking=False):
                        if system_mode == MODE_REC:
                            tracks[current_track_idx] = []
                            print(f"Recording Track {current_track_idx + 1}...")
                        else:
                            print("Playing...")

                        is_running = True
                        is_paused = False
                        start_time = time.perf_counter()
                        total_pause_duration = 0
                        
                        t_seq = threading.Thread(target=sequencer_thread, daemon=True)
                        t_seq.start()
                        
                        # Release lock after a short delay (thread has started)
                        threading.Timer(0.1, sequencer_lock.release).start()
                    
                update_ui()

            # 3. LEFT / PAUSE
            if s_left == 0 and last_states[BTN_L_PAUSE] == 1:
                if not is_running:
                    current_track_idx = (current_track_idx - 1) % 10
                    print(f"Track {current_track_idx + 1}")
                    update_ui()
                else:
                    is_paused = not is_paused
                    print("Paused" if is_paused else "Resuming...")
                    update_ui()

            # 4. RIGHT / CLEAR TRACK
            if s_right == 0 and last_states[BTN_R_CLEAR] == 1:
                if not is_running:
                    current_track_idx = (current_track_idx + 1) % 10
                    print(f"Track {current_track_idx + 1}")
                    update_ui()
                elif system_mode == MODE_PLAY:
                    tracks[current_track_idx] = []
                    flash_led(LED_CLEAR)
                    print(f"Track {current_track_idx + 1} Cleared!")

            # 5. DELETE ALL (Touch Sensor)
            if s_touch == 1 and last_states[TOUCH_SENSOR] == 0:
                is_running = False
                is_paused = False
                midi_panic()
                tracks = [[] for _ in range(10)]
                flash_led(LED_DELETE_ALL, duration=1.0)
                print("--- ALL TRACKS DELETED ---")
                update_ui()

            last_states[BTN_MODE] = s_mode
            last_states[BTN_ACTION] = s_action
            last_states[BTN_L_PAUSE] = s_left
            last_states[BTN_R_CLEAR] = s_right
            last_states[TOUCH_SENSOR] = s_touch
            
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("Shutdown...")

# --- MAIN STARTUP ---
if __name__ == "__main__":
    setup_gpio()
    update_ui()
    
    try:
        in_port_name, out_port_name = get_midi_ports()
        
        if in_port_name and out_port_name:
            midi_in = mido.open_input(in_port_name)
            midi_out = mido.open_output(out_port_name)
            
            t_rec = threading.Thread(target=midi_recorder, daemon=True)
            t_rec.start()
            
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
        midi_panic()
        GPIO.cleanup()
        if midi_in: 
            midi_in.close()
        if midi_out: 
            midi_out.close()