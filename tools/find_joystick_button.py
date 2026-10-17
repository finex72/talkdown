"""Zeigt alle Joysticks und welche Taste (Nummer) du gerade drückst."""
import os
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
import pygame

pygame.init()
pygame.joystick.init()
sticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
if not sticks:
    print("Kein Joystick gefunden.")
    raise SystemExit
for i, j in enumerate(sticks):
    print(f"Joystick {i}: {j.get_name()} ({j.get_numbuttons()} Tasten)")
print("\nDrücke jetzt deine PTT-Taste. Beenden mit Strg+C.\n")
try:
    while True:
        for e in pygame.event.get():
            if e.type == pygame.JOYBUTTONDOWN:
                print(f"Joystick {e.instance_id}  Taste {e.button}  gedrückt")
            elif e.type == pygame.JOYBUTTONUP:
                print(f"Joystick {e.instance_id}  Taste {e.button}  losgelassen")
            elif e.type == pygame.JOYHATMOTION and e.value != (0, 0):
                print(f"Joystick {e.instance_id}  Hat {e.hat}  {e.value}")
        pygame.time.wait(10)
except KeyboardInterrupt:
    pass
