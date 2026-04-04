#!/usr/bin/env python3

import requests
from pynput import keyboard

def send_event(event):
    try:
        response = requests.post('http://localhost:5003/event', json={'event': event})
        print(f"Sent event: {event}, Status: {response.status_code}")
    except Exception as e:
        print(f"Failed to send event: {e}")

def on_press(key):
    if key == keyboard.Key.space:
        send_event('EV_KEY_SPACE_DOWN')
    elif hasattr(key, 'char') and key.char == 'm':
        send_event('EV_KEY_M')
    elif key == keyboard.Key.esc:
        return False  # Stop listener

def on_release(key):
    if key == keyboard.Key.space:
        send_event('EV_KEY_SPACE_UP')

if __name__ == "__main__":
    print("Starting keyboard simulator. Press Esc to exit.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()