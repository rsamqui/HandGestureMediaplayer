import cv2
import mediapipe as mp
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
import math

# --- CONFIGURACIÓN DE SPOTIPY ---
os.environ["SPOTIPY_CLIENT_ID"] = "cad3e41b54924f61a13c047a53603920"
os.environ["SPOTIPY_CLIENT_SECRET"] = "492175ad65fc464a9c94c75d25008c60"
os.environ["SPOTIPY_REDIRECT_URI"] = "http://127.0.0.1:8888/callback" # O el que configuraste

# Define los 'scopes' (permisos) que necesitas.
SCOPE = "user-modify-playback-state user-read-playback-state user-library-read user-library-modify"

# Configura la autenticación
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope=SCOPE, show_dialog=True))

# --- FIN CONFIGURACIÓN SPOTIPY ---

# Inicializar MediaPipe Hands
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(min_detection_confidence=0.7, min_tracking_confidence=0.7)
mp_drawing = mp.solutions.drawing_utils

# Inicializar OpenCV VideoCapture
cap = cv2.VideoCapture(0)

# Variables para el control de gestos
DEFAULT_COOLDOWN = 2   
NEXT_SONG_COOLDOWN = 4 
PLAY_PAUSE_COOLDOWN = 3 
VOLUME_COOLDOWN = 1.5
PREVIOUS_SONG_COOLDOWN = 4
LIKE_SONG_COOLDOWN = 2.5
UNLIKE_SONG_COOLDOWN = 2.5
gesture_cooldown = DEFAULT_COOLDOWN
last_gesture_time = 0

def get_active_device_id(spotify_client):
    """Obtiene el ID del dispositivo activo o el primero disponible."""
    devices = spotify_client.devices()
    if devices and devices['devices']:
        for device in devices['devices']:
            if device['is_active']:
                return device['id']
        # Si no hay activo, devuelve el primero
        return devices['devices'][0]['id']
    print("No se encontraron dispositivos Spotify activos/disponibles.")
    return None

# Calculo de distancia entre dos puntos
def get_distance(p1, p2):
    """Calcula la distancia euclidiana entre dos puntos de MediaPipe."""
    return math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2)

def recognize_gesture(hand_landmarks):
    """
    Reconoce un gesto (L-Shape para Unlike).
    """
    landmarks = hand_landmarks.landmark
    
    # Puntos Clave - Puntas
    thumb_tip = landmarks[mp_hands.HandLandmark.THUMB_TIP]
    index_tip = landmarks[mp_hands.HandLandmark.INDEX_FINGER_TIP]
    middle_tip = landmarks[mp_hands.HandLandmark.MIDDLE_FINGER_TIP]
    ring_tip = landmarks[mp_hands.HandLandmark.RING_FINGER_TIP]
    pinky_tip = landmarks[mp_hands.HandLandmark.PINKY_TIP]

    # Puntos Clave - PIPs
    thumb_pip = landmarks[mp_hands.HandLandmark.THUMB_IP] 
    index_pip = landmarks[mp_hands.HandLandmark.INDEX_FINGER_PIP]
    middle_pip = landmarks[mp_hands.HandLandmark.MIDDLE_FINGER_PIP]
    ring_pip = landmarks[mp_hands.HandLandmark.RING_FINGER_PIP]
    pinky_pip = landmarks[mp_hands.HandLandmark.PINKY_PIP]

    # Puntos Clave - Distancia
    wrist = landmarks[mp_hands.HandLandmark.WRIST]
    middle_mcp = landmarks[mp_hands.HandLandmark.MIDDLE_FINGER_MCP]

    # --- Lógica de Detección ---

    # ¿Está cada dedo extendido? (Usamos y < pip como chequeo principal)
    is_thumb_extended = thumb_tip.y < thumb_pip.y 
    is_index_extended = index_tip.y < index_pip.y
    is_middle_extended = middle_tip.y < middle_pip.y
    is_ring_extended = ring_tip.y < ring_pip.y
    is_pinky_extended = pinky_tip.y < pinky_pip.y
    num_main_fingers_extended = sum([is_index_extended, is_middle_extended, is_ring_extended, is_pinky_extended])

    # Distancias
    ref_dist = get_distance(wrist, middle_mcp)
    if ref_dist < 0.01: ref_dist = 0.01 
    dist_tip_wrist = get_distance(index_tip, wrist)
    dist_thumb_index = get_distance(thumb_tip, index_tip)

    # Umbrales
    OPEN_THRESHOLD = 1.8  
    OK_THRESHOLD = 0.15 # <-- Umbral para Gesto OK (AJUSTA ESTE VALOR)

    # --- Asignación de Gestos ---

    # 1. Gesto de Siguiente (Mano abierta)
    if num_main_fingers_extended == 4 and (dist_tip_wrist / ref_dist) > OPEN_THRESHOLD:
        print("Detectando: King Baldwin (Play/Pause)")
        return "play_pause"

    # 2. Gesto de Anterior (Shaka)
    if is_thumb_extended and not is_index_extended and not is_middle_extended and not is_ring_extended and is_pinky_extended:
        print("Detectando: Shaka (Previous)")
        return "previous"

    # 3. Gesto de Subir Volumen (Índice, Corazón, Meñique arriba)
    if is_index_extended and is_middle_extended and not is_ring_extended and is_pinky_extended:
        print("Detectando: West Coast (Volume Down)")
        return "volume_down"
        
    # 4. Gesto de Next (Paz/Victoria)
    if (is_index_extended and is_middle_extended and 
            not is_ring_extended and not is_pinky_extended and
            (dist_thumb_index / ref_dist) > OK_THRESHOLD):
        print("Detectando: Paz (Next Song)")
        return "next"

    # 5. Gesto de Bajar Volumen (Índice y Meñique arriba - "Rock")
    if is_index_extended and not is_middle_extended and not is_ring_extended and is_pinky_extended:
        print("Detectando: Rock (Volume Up)")
        return "volume_up"

    # 6. Gesto de Like (OK: Pulgar e Índice cerca Y los otros 3 extendidos)
    #    Aseguramos que el índice NO esté totalmente extendido (porque está curvado)
    if ((dist_thumb_index / ref_dist) < OK_THRESHOLD and
            is_middle_extended and is_ring_extended and is_pinky_extended and
            not is_index_extended):
        print("Detectando: OK (Like Song)")
        return "like_song"

    # 7. Gesto de Anular Like (L-Shape)
    if is_thumb_extended and is_index_extended and not is_middle_extended and not is_ring_extended and not is_pinky_extended:
        print("Detectando: L-Shape (Unlike Song)")
        return "unlike_song"

    return None

def control_spotify(gesture, spotify_client):
    """Controla Spotify basado en el gesto."""
    try:
        device_id = get_active_device_id(spotify_client)
        
        playback = spotify_client.current_playback()
        current_volume = 100 
        is_playing = False
        track_id = None

        if playback:
            if playback['device']:
                 current_volume = playback['device']['volume_percent']
            is_playing = playback['is_playing']
            if playback['item']:
                track_id = playback['item']['id']

        if not device_id and gesture not in ["like_song"]:
            return

        if gesture == "play_pause":
            if not device_id: return
            if is_playing:
                spotify_client.pause_playback(device_id=device_id)
                print("Spotify Paused")
            else:
                spotify_client.start_playback(device_id=device_id)
                print("Spotify Play")

        elif gesture == "next":
            if not device_id: return
            spotify_client.next_track(device_id=device_id)
            print("Spotify Next Track")

        elif gesture == "previous":
            if not device_id: return
            spotify_client.previous_track(device_id=device_id)
            print("Spotify Previous Track")

        elif gesture == "like_song":
            if track_id:
                try:
                    # Comprobar si ya está guardada (opcional, pero evita errores)
                    is_saved = spotify_client.current_user_saved_tracks_contains(tracks=[track_id])
                    if not is_saved[0]:
                         spotify_client.current_user_saved_tracks_add(tracks=[track_id])
                         print(f"Spotify Song Liked! (ID: {track_id})")
                    else:
                         print("La canción ya estaba en 'Me Gusta'.")
                except Exception as e:
                    print(f"Error al dar 'Like': {e}")
            else:
                print("No hay canción sonando para dar 'Like'.")
        
        elif gesture == "unlike_song":
            if track_id:
                try:
                    is_saved = spotify_client.current_user_saved_tracks_contains(tracks=[track_id])
                    if is_saved[0]: # Solo quitar si está guardada
                         spotify_client.current_user_saved_tracks_delete(tracks=[track_id])
                         print(f"Spotify Song Unliked! (ID: {track_id})")
                    else:
                         print("La canción no estaba en 'Me Gusta'.")
                except Exception as e:
                    print(f"Error al quitar 'Like': {e}")
            else:
                print("No hay canción sonando para quitar 'Like'.")

        elif gesture == "volume_up":
            if not device_id: return
            new_volume = min(current_volume + 10, 100)
            spotify_client.volume(new_volume, device_id=device_id)
            print(f"Spotify Volume Up: {new_volume}%")
        
        elif gesture == "volume_down":
            if not device_id: return
            new_volume = max(current_volume - 10, 0)
            spotify_client.volume(new_volume, device_id=device_id)
            print(f"Spotify Volume Down: {new_volume}%")

    except Exception as e:
        print(f"Error al controlar Spotify: {e}")
        print("Asegúrate de que Spotify esté abierto y reproduciendo en algún dispositivo.")


# --- Bucle Principal (CORREGIDO con Lógica de Cooldown) ---
while cap.isOpened():
    success, image = cap.read()
    if not success:
        print("No se pudo leer el cuadro de la cámara.")
        break

    image = cv2.flip(image, 1)
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    results = hands.process(image_rgb)

    current_gesture_display = "NINGUNO" # Para mostrar en pantalla

    if results.multi_hand_landmarks:
        for hand_landmarks in results.multi_hand_landmarks:
            mp_drawing.draw_landmarks(
                image, hand_landmarks, mp_hands.HAND_CONNECTIONS)

            current_time = time.time()
            if current_time - last_gesture_time > gesture_cooldown:
                gesture = recognize_gesture(hand_landmarks)
                if gesture:
                    # Gesto detectado, realizar acción y actualizar tiempos/cooldowns
                    print(f"--- Acción: {gesture} ---") # Mensaje más claro
                    control_spotify(gesture, sp) 
                    last_gesture_time = current_time
                    current_gesture_display = gesture 
                    
                    # --- LÓGICA DE MÚLTIPLES COOLDOWNS ---
                    new_cooldown = gesture_cooldown 

                    if gesture == "next":
                        new_cooldown = NEXT_SONG_COOLDOWN
                    elif gesture == "previous": 
                        new_cooldown = PREVIOUS_SONG_COOLDOWN
                    elif gesture == "play_pause":
                        new_cooldown = PLAY_PAUSE_COOLDOWN
                    elif gesture == "volume_up" or gesture == "volume_down":
                        new_cooldown = VOLUME_COOLDOWN
                    elif gesture == "like_song":
                        new_cooldown = LIKE_SONG_COOLDOWN
                    elif gesture == "unlike_song":
                        new_cooldown = UNLIKE_SONG_COOLDOWN
                    else: 
                        new_cooldown = DEFAULT_COOLDOWN

                    # Solo imprimimos si el cooldown cambia
                    if new_cooldown != gesture_cooldown:
                        gesture_cooldown = new_cooldown
                        print(f"Cooldown cambiado a {gesture_cooldown}s")
                    # --- FIN LÓGICA ---

    # --- Añadir Texto a la Imagen ---
    cv2.putText(image, 
                f"Gesto: {current_gesture_display}", 
                (10, 50), 
                cv2.FONT_HERSHEY_SIMPLEX, 
                1, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.putText(image, 
                f"Cooldown: {gesture_cooldown:.1f}s", 
                (10, 100), 
                cv2.FONT_HERSHEY_SIMPLEX, 
                0.8, (255, 255, 0), 2, cv2.LINE_AA)
    # --- Fin Añadir Texto ---

    cv2.imshow('Gesture Controlled Spotify', image)

    if cv2.waitKey(5) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()