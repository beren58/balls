"""
TREBLEMAKER  -  a terminal spell-battle game.

Run in a terminal window that is at least 102 x 33 characters.

Flow:  falling-cat intro -> title menu -> (new game: pick character + name)
       -> hub (battle / achievements / customize / spells).

Controls:  arrow keys or W/S (and A/D in grids) = move,  ENTER = select,
           Q or ESC = back,  number keys 1-8 = play notes in battle.

Needs only the Python standard library, plus the optional "pygame" package
for sound (pip install pygame-ce). Without it the game runs silently.
Put the .wav files next to this script (or in a "sounds" folder beside it).
The game creates its own save file (savegame.json, next to this script)
the first time you start a new game - you never have to create it yourself.
"""
# ==============================================================================
# HOW THIS WHOLE FILE WORKS (read this first)
# ==============================================================================
# The game is built on three simple ideas:
#
# 1. FRAME-BASED RENDERING. Every screen is built as a Python list of strings, one string per
# terminal row. show(lines) prints the entire list in one go. Animation is nothing more than
# "build a slightly different list, print it again" over and over (about 10-20 times per second).
#
# 2. NON-BLOCKING KEYBOARD INPUT. Normal input() freezes the program until Enter is pressed. The
# Keys class instead reads ONE key press at a time, instantly, and keys.get(timeout) gives up and
# returns None after `timeout` seconds. That timeout doubles as the frame delay, so a loop like
# "draw; wait up to 0.1 s for a key; repeat" can animate AND listen at the same time.
#
# 3. DATA-DRIVEN CONTENT. Characters, monsters and achievements are plain dictionaries / tuples near
# the top. The game logic only READS them, so adding a monster or achievement means adding data,
# not changing logic.
#
# PROGRAM FLOW:
# main() -> falling_intro() -> title_loop() -> (new game: create_character()) -> hub()
# hub() -> battle() -> fight() (called once per monster, until you lose or flee)
# hub() -> achievements_menu() / customize_menu() / spells_menu()
#
# Imports (all standard library; pygame is imported lazily later, only if it exists):
# json    - read/write the save file and settings file
# os      - platform check (os.name), raw byte reads, os.system("") trick for Windows colors
# random  - monster choice, note sequences, decorative scenery
# re      - regular expression used to strip color codes when measuring text width
# shutil  - shutil.get_terminal_size() tells us the window size
# sys     - sys.stdout (drawing), sys.stdin (keyboard), sys.exit-style checks
# time    - timers for animation, monster walking speed, sleeping
# pathlib.Path - object-style file paths (Path / "name", .exists(), .read_text(), ...)
import json
import os
import random
import re
import shutil
import sys
import time
from pathlib import Path

# =============================================================================
# 1. SETTINGS  (safe to tweak)
# =============================================================================
# Path(__file__) is the location of this script; with_name() swaps the file name,
# so the save file always sits in the same folder as the game.
# Path(__file__) is the location of THIS script. with_name("savegame.json") replaces just the file
# name, so the save file always lives next to the game no matter which folder you launch it from.
SAVE_FILE = Path(__file__).with_name("savegame.json")
# Levelling rule used by level_of(): level = 1 + total_score // 150 (integer division). So 0-149
# points = level 1, 150-299 = level 2, and so on. Level also unlocks characters for free.
POINTS_PER_LEVEL = 150          # player level = 1 + total_score // POINTS_PER_LEVEL

# Display names of the 8 notes. The player presses keys 1-8, and key "n" corresponds to NOTES[n - 1]
# (lists start at index 0, which is why the code subtracts 1 everywhere).
NOTES = ["DO", "RE", "MI", "FA", "SO", "LA", "SI", "DO'"]    # keys 1-8
# A set of the characters "1".."8". Checking `k in NOTE_KEYS` is a fast way to ask "is this key
# one of the note keys?" (set(...) of a string splits it into single characters.)
NOTE_KEYS = set("12345678")

# Opening animation
# Opening animation timing. The idle cat has two poses (eyes open / eyes closed). The pose shown is
# chosen with int(time / IDLE_SWITCH_SECONDS) % number_of_poses, so it flips every this-many
# seconds.
IDLE_SWITCH_SECONDS = 1.0       # how often the idle cat flips between its 2 poses
# How long each frame of the falling intro lasts. The cat drops one row per frame, so a bigger
# number means a slower fall.
FALL_DELAY = 0.12               # seconds per row the cat drops (bigger = slower)
# How long the "landed" pose is held before the title menu appears.
LANDING_SECONDS = 1.2           # how long the landing pose stays before the menu

# Battle animation
# Battle animation settings. CAT_X is the screen column where the left edge of the cat is drawn.
CAT_X = 3                       # column where the cat stands in the battle scene
# After each note press the cat switches to its ATTACK ("pushing") pose for this long, then goes
# back to idle.
ATTACK_SECONDS = 0.18           # how long the "push" pose lasts after each note
# The tip of the paw, measured inside the ATTACK drawing (column 30, row 2). Note projectiles are
# spawned exactly there so the "~~DO" text looks like it leaves the paw.
PAW_COL, PAW_ROW = 30, 2        # where the paw tip is inside the ATTACK drawing
# How fast a note projectile flies, in screen columns per second. Position is computed as
# x = start_x + (now - spawn_time) * PROJECTILE_SPEED, so speed is independent of the frame rate.
PROJECTILE_SPEED = 110          # columns per second the note "bullet" flies

# =============================================================================
# 2. COLORS  (ANSI escape codes)
# =============================================================================
# Terminals understand special text sequences that start with the ESC character
# ("\033"). "\033[31m" means "from now on print in red", "\033[0m" resets.
# We put these codes *inside* our strings; they are invisible but change style.
# ANSI ESCAPE CODES. "\033" is the ESC character (octal 33 = decimal 27). The terminal treats
# ESC + "[" + numbers + a letter as a command instead of printing it. For example "\033[31m" means
# "print everything after this in red". These codes take up characters in the Python string but
# NOT on screen, which is why vlen()/pad() exist (they ignore the codes when measuring width).
# 0 = reset all styles, 1 = bold, 2 = dim (faint), 7 = reverse (swap foreground/background; used
# to highlight the selected menu entry).
RESET, BOLD, DIM, REVERSE = "\033[0m", "\033[1m", "\033[2m", "\033[7m"
# Foreground colors: 31 red, 32 green, 33 yellow, 36 cyan. Always end a colored piece of text
# with RESET, otherwise the color "leaks" into everything printed afterwards.
RED, GREEN, YELLOW, CYAN = "\033[31m", "\033[32m", "\033[33m", "\033[36m"

# =============================================================================
# 3. YOUR ART
# =============================================================================
# r"""...""" is a *raw* triple-quoted string: backslashes stay exactly as drawn
# and the string can span many lines. Spacing matters!

# ASCII ART. Each drawing is a RAW triple-quoted string (r followed by three quote marks):
# backslashes
# are kept literally (so a backslash in the drawing is not read as an escape sequence) and the
# string may span many lines. SPACING IS PART OF THE
# DRAWING, do not re-indent these blocks. The string starts with a newline (right after the opening
# quotes), which art_lines() later removes.
#
# IDLE / IDLE_2: the two standing poses. They differ only in the eyes ("8 8" vs "- -") and slightly
# in
# the legs, so flipping between them makes the cat look like she blinks.
IDLE = r"""
         /\      /\
        /( \----/ )\
       .     vv     .
   _ >(   8       8  )<
  \ \  .      ^     .
  / /    /        \
 / /    /          \
 \ \   /            \
  \ \ /       n n    \
"""

# Second idle pose (closed eyes). Compare with IDLE above.
IDLE_2 = r"""
         /\      /\
        /( \----/ )\
       .     vv     .
   _ >(   -       -  )<
  \ \  .      ^     .
   \ \   /        \
   / /  /          \
  / /  /            \
  \ \ /       n n    \
"""

# The "pushing" pose: the arm is stretched out to the right (the "nn" and "</ /" shapes) and a
# "|" line shows the other arm. It is shown for ATTACK_SECONDS after every note press.
# The paw tip position (PAW_COL, PAW_ROW) refers to this drawing.
ATTACK = r"""
             /\      /\
            /( \----/ )\
           .     vv     .   nn
   _     >(   >       <  )</ /
  \ \      .      A     . / /
  / /       /         |  / /
 / /      /           | / /
 \ \    /             |/ /
  \ \ /               |
"""

# Fall scene: four frames that cycle while the cat falls, then the landing pose.
# FALL SCENE: four frames of the cat falling (the "o" and "//" parts are the cat's tail swishing).
# They are shown one after another in a cycle, so the tail seems to wave while the cat drops.
FALL_1 = r"""
                     o
         _/\        //
     ___( * \______//__
    I---___________----I
"""

FALL_2 = r"""
                    o
         _/\       //
     ___( * \______\\__
    I---___________----I
"""

FALL_3 = r"""
                  o
         _/\      \\
     ___( * \______\\__
    I---___________----I
"""

FALL_4 = r"""
                   o
         _/\       \\
     ___( * \______//__
    I---___________----I
"""

# The pose after landing. It is the same cat as the one drawn inside MENU below, which is how the
# intro and the title screen connect without a visible jump.
LANDING = r"""
         _ /|
        ( * |   0
         |   \  ))
         |    \((
        n| (   \))
"""

# The list the intro loops over: frame number = row_number % 4.
FALL_FRAMES = [FALL_1, FALL_2, FALL_3, FALL_4]

# Main menu. It appears after the cat lands, and the landing pose is part of it.
# THE TITLE SCREEN, drawn by hand as one big 102 x 33 picture: border, "TREBLEMAKER" title letters,
# the four labels ">CONTINUE<", ">NEW GAME<", ">OPTIONS<", ">TUTORIAL<", and the cat plus ground.
#
# IMPORTANT: this single drawing also defines the size of EVERY screen in the game (see MENU_W and
# MENU_H below). If you edit it, keep all lines the same length.
MENU = r"""
------------------------------------------------------------------------------------------------------
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|       _______ ______  ______ _____   __      ______ ___  ___    ___    __ ___ ______ ______        |
|         ||    ||   )) ||     ||   )) ||      ||     ||\  /||   // \\   ||_//  ||     ||   ))       |
|         ||    ||---\  ||---  ||---<< ||      ||---  || \/ ||  //---\\  ||--\  ||---  ||---\        |
|         ||    ||   \\ ||____ ||___)) ||____| ||____ ||    || //     \\ ||  \\ ||____ ||   \\       |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|    >CONTINUE<                                                                                      |
|                                                                                                    |
|    >NEW GAME<                                                                                      |
|                                                                    _ /|                            |
|    >OPTIONS<                                                      ( * |   0                        |
|                                                                    |   \  ))                       |
|    >TUTORIAL<                                                  __  |    \((  __                    |
|                                                        __   ______n| (   \))_____    __            |
|                                                                __     __      ____                 |
|                                                           __        ____     __                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
|                                                                                                    |
------------------------------------------------------------------------------------------------------
"""


# Converts a multi-line art string to a list of lines (one string per row).
# text.strip("\n") removes ONLY newline characters at both ends. If we used strip() it would also
# delete the leading spaces of the first row and wreck the alignment of the drawing.
def art_lines(text):
    """Turn a multi-line art string into a list of lines.
    strip("\\n") removes only the empty first/last line - NOT the spaces."""
    return text.strip("\n").split("\n")


# Derived sizes used all over the program:
# MENU_LINES  - the title screen as a list of rows
# MENU_W/MENU_H - width/height of the whole frame (102 x 33). Every screen is this size.
# INNER_W/INNER_H - the room INSIDE the border (one column lost on each side, one row on top, one
# at the bottom).
MENU_LINES = art_lines(MENU)
MENU_W = max(len(line) for line in MENU_LINES)   # 102
MENU_H = len(MENU_LINES)                         # 33
INNER_W, INNER_H = MENU_W - 2, MENU_H - 2        # the space *inside* the frame

# TITLE = the 8 rows (3..10) of the title letters taken out of the menu drawing. [1:-1] chops off
# the
# "|" border characters at both ends of each row. These rows become a header image on other screens.
# Rows 3-10 of the menu are the big "CAT GAME" letters. We reuse them as the
# header of every other screen, so the whole game shares one look.
TITLE = [line[1:-1] for line in MENU_LINES[3:11]]   # [1:-1] cuts off the two "|"

# The cat's poses in list form, ready for use: two idle poses (that flip) and one attack pose.
CAT_IDLE = [art_lines(IDLE), art_lines(IDLE_2)]
CAT_ATTACK = art_lines(ATTACK)

# Effects art. Each effect is a list of FRAMES (each frame is a list of text rows). The battle code
# shows one frame at a time to build a tiny animation.
# ---- extra art I added for the battle scene ---------------------------------
# Small hit sparks (2 frames) and big explosions (3 frames). The battle code
# plays these frames one after another to make an animation.
# Small hit spark when a note hits the monster (2 frames: a star burst, then fading dots).
SPARK_FRAMES = [
    art_lines(r"""
\|/
-*-
/|\
"""),
    art_lines(r"""
 . '
'   .
 ' .
"""),
]
# Large explosion (3 frames: burst, big ring, fading debris) for a completed sequence / kill.
BOOM_FRAMES = [
    art_lines(r"""
  \ | /
 -- * --
  / | \
"""),
    art_lines(r"""
 .  \ | /  .
  \ \ | / /
 --- (O) ---
  / / | \ \
 '  / | \  '
"""),
    art_lines(r"""
 .  '   .  '
  ' .  '  .
 .   *  *   '
  '  .  '  .
 .  '   .  '
"""),
]
# Replaces the cat when she dies.
TOMBSTONE = art_lines(r"""
   _______
  /       \
 /   RIP   \
 |         |
 |  x   x  |
_|_________|_
""")
# Cloud scenery (2 rows). Clouds slowly scroll left in the battle background.
CLOUD = [" .-~~-. ", "(______)"]

# Background mountains: one small 3-row "tile" that is repeated horizontally to fill the screen
# width (see MOUNTAINS in the battle section).
# Mountains in the background: one tile that we repeat across the screen.
_TILE = [
    r"       /\                /\          ",
    r"  /\  /  \      /\      /  \    /\   ",
    r" /  \/    \_/\_/  \_/\_/    \__/  \_ ",
]

# BIGFONT: a tiny font. Each letter is 5 rows tall and 5 columns wide (space is 3 wide). Only the
# letters of "YOU DIED" are defined, adding other letters means adding entries here.
# Big block letters for the "YOU DIED" banner.
BIGFONT = {
    "Y": ["Y   Y", " Y Y ", "  Y  ", "  Y  ", "  Y  "],
    "O": [" OOO ", "O   O", "O   O", "O   O", " OOO "],
    "U": ["U   U", "U   U", "U   U", "U   U", " UUU "],
    "D": ["DDDD ", "D   D", "D   D", "D   D", "DDDD "],
    "I": ["IIIII", "  I  ", "  I  ", "  I  ", "IIIII"],
    "E": ["EEEEE", "E    ", "EEEE ", "E    ", "EEEEE"],
    " ": ["   ", "   ", "   ", "   ", "   "],
}


# Builds a big-letter banner. For each of the 5 rows we take row `row` of every letter and join the
# pieces with two spaces between letters, so the result is 5 text lines. Example: banner("YOU") ->
# the three glyphs side by side. A character that is not in BIGFONT would raise a KeyError.
def banner(text):
    """Join the big letters side by side -> 5 lines of text."""
    return ["  ".join(BIGFONT[ch][row] for ch in text) for row in range(5)]


# =============================================================================
# 4. CHARACTERS  (replace the placeholder art with your own designs)
# =============================================================================
# "idle"     = list of poses that flip back and forth
# "attack"   = pose used when the player pushes a note
# "released" = False means "locked for now" (cannot be picked or bought yet).
#              Flip it to True when you have finished the character's art.
# "price"/"level" = unlock cost in coins, or the level that unlocks it for free.
# All playable characters, keyed by a short id. Fields of each entry:
# name      - what the player sees
# released  - False = placeholder, cannot be picked or bought yet. Only the cat is finished.
# price     - cost in coins to unlock
# level     - player level that unlocks it for free (whichever is reached first)
# idle      - list of poses that alternate when standing
# attack    - OPTIONAL pose when pushing a note (cat only for now)
# Unfinished characters use a plain placeholder box as art.
CHARACTERS = {
    "cat": {"name": "Cat", "released": True, "price": 0, "level": 1,
            "idle": CAT_IDLE, "attack": CAT_ATTACK},
    "dog": {"name": "Dog", "released": False, "price": 0, "level": 1,
            "idle": [art_lines(r"""
     ______
    //____\\
   |   __   |
   |   ||   |
   |________|
   
   
""")]},
    "monkey": {"name": "Monkey", "released": False, "price": 300, "level": 3,
               "idle": [art_lines(r"""
     ______
    //____\\
   |   __   |
   |   ||   |
   |________|
    
    
""")]},
    "snake": {"name": "Snake", "released": False, "price": 400, "level": 4,
              "idle": [art_lines(r"""
     ______
    //____\\
   |   __   |
   |   ||   |
   |________|
   
   
""")]},
    "elephant": {"name": "Elephant", "released": False, "price": 500, "level": 6,
                 "idle": [art_lines(r"""
     ______
    //____\\
   |   __   |
   |   ||   |
   |________|
   
   
""")]},
    "tiger": {"name": "Tiger", "released": False, "price": 700, "level": 8,
              "idle": [art_lines(r"""
     ______
    //____\\
   |   __   |
   |   ||   |
   |________|
   
    
""")]},
}
# setdefault(key, value) only sets the key if it is MISSING. So every character without a drawn
# attack pose gets its first idle pose as the attack pose and the battle code never needs to check
# whether "attack" exists.
for _c in CHARACTERS.values():
    _c.setdefault("attack", _c["idle"][0])    # no attack pose drawn yet? reuse idle
# The height (in rows) of the tallest character sprite. grid() uses it to bottom-align every
# character in the selection grids no matter how tall the individual art is.
ART_H = max(len(c["idle"][0]) for c in CHARACTERS.values())

# =============================================================================
# 5. MONSTERS  (name, hp, killer spell, art ...)
# =============================================================================
# "cast" = the short note combo of the killer spell (instant kill, bought in
# the spell book). "min_stage" = how far into a run before it can show up.
# All monsters. Fields:
# hp         - how many COMPLETE note sequences must be played to kill it
# min_stage  - first stage (0-based) at which it can appear
# coins      - reward for the kill
# spell      - name of its killer spell, "cast" is the note combo (key numbers) and "price" the cost
# in the Spell Book. An owned spell kills the monster in one go.
# art        - sprite (list of rows via art_lines)
# Stronger monsters have more hp, longer spell combos and more expensive spells.
MONSTERS = {
    "slime": {"name": "Slime", "hp": 1, "min_stage": 0, "coins": 5,
              "spell": "Salt Splash", "cast": [1, 2, 3], "price": 30,
              "art": art_lines(r"""
   ,-~~~-.
  /  o o  \
 (    v    )
  `-.___.-'
""")},
    "goblin": {"name": "Goblin", "hp": 2, "min_stage": 1, "coins": 8,
               "spell": "Goblin Bane", "cast": [3, 5, 1], "price": 50,
               "art": art_lines(r"""
 /\_   _/\
 \  \_/  /
 ( o   o )
  \  v  /
  /|___|\
 / /   \ \
""")},
    "bat": {"name": "Bat", "hp": 2, "min_stage": 3, "coins": 10,
            "spell": "Sonic Screech", "cast": [8, 6, 8], "price": 70,
            "art": art_lines(r"""
 /\   /\
/  \_/  \
\ (o o) /
 \/ v \/
   \_/
""")},
    "skeleton": {"name": "Skeleton", "hp": 3, "min_stage": 5, "coins": 14,
                 "spell": "Bone Breaker", "cast": [4, 4, 7, 2], "price": 100,
                 "art": art_lines(r"""
  .---.
 ( o o )
  \ = /
 _|]#[|_
/ |]#[| \
  _| |_
""")},
    "ogre": {"name": "Ogre", "hp": 4, "min_stage": 8, "coins": 20,
             "spell": "Giant's Fall", "cast": [2, 6, 1, 5], "price": 150,
             "art": art_lines(r"""
  ,-----.
 / >   < \
(   ___   )
 \_/___\_/
 /|#####|\
/ |#####| \
  /_/ \_\
""")},
    "dragon": {"name": "Dragon", "hp": 6, "min_stage": 12, "coins": 40,
               "spell": "Dragon's Lullaby", "cast": [5, 3, 8, 1, 6], "price": 300,
               "art": art_lines(r"""
     /\_/\      __
    ( @ @ )____/  \
  /|  \_/  ____  /
 / |__/ \_/    \/
/_/ /_/ \_\ \_\
""")},
}


# =============================================================================
# 6. ACHIEVEMENTS  (id, name, description, coin reward, check function)
# =============================================================================
# Player level from the save dictionary. // is integer division, so 149 // 150 = 0 -> level 1.
def level_of(save):
    return 1 + save["total_score"] // POINTS_PER_LEVEL


# A "lambda" is a tiny one-line function: lambda s: s["total_kills"] >= 1
# means "given the save s, return True if the player has at least 1 kill".
# Storing one per achievement lets the game ask "is it complete?" later.
# Each achievement is a tuple: (id, name, description, coin reward, check function).
# The check is a LAMBDA: a tiny nameless function. `lambda s: s["total_kills"] >= 1` means "given
# the
# save dictionary s, return True if the player has at least 1 kill". Storing the function inside
# the tuple lets the menu simply call check(save) without knowing what each achievement measures.
# kills.get("slime", 0) means "kills of slimes, or 0 if the player never killed one" (so no
# KeyError for monster types that were never defeated).
ACHIEVEMENTS = [
    ("first_blood", "First Blood", "Defeat your first monster", 20,
     lambda s: s["total_kills"] >= 1),
    ("slayer", "Slayer", "Defeat 25 monsters", 60,
     lambda s: s["total_kills"] >= 25),
    ("hunter", "Monster Hunter", "Defeat 100 monsters", 200,
     lambda s: s["total_kills"] >= 100),
    ("score_100", "Getting Warm", "Score 100 points in one run", 40,
     lambda s: s["max_score"] >= 100),
    ("score_500", "On Fire", "Score 500 points in one run", 150,
     lambda s: s["max_score"] >= 500),
    ("level_3", "Apprentice", "Reach level 3", 50,
     lambda s: level_of(s) >= 3),
    ("level_5", "Adept", "Reach level 5", 120,
     lambda s: level_of(s) >= 5),
    ("spend_100", "Shopper", "Spend 100 coins in total", 50,
     lambda s: s["coins_spent"] >= 100),
    ("spend_500", "Big Spender", "Spend 500 coins in total", 150,
     lambda s: s["coins_spent"] >= 500),
    ("slimes_10", "Slime Squasher", "Defeat 10 slimes", 40,
     lambda s: s["kills"].get("slime", 0) >= 10),
    ("goblins_10", "Goblin Menace", "Defeat 10 goblins", 60,
     lambda s: s["kills"].get("goblin", 0) >= 10),
    ("dragons_3", "Dragon Slayer", "Defeat 3 dragons", 200,
     lambda s: s["kills"].get("dragon", 0) >= 3),
    ("stage_10", "Survivor", "Reach stage 10 in one run", 100,
     lambda s: s["max_stage"] >= 10),
    ("games_10", "Regular", "Play 10 battles", 30,
     lambda s: s["games_played"] >= 10),
]


# =============================================================================
# 7. SAVE / LOAD
# =============================================================================
# Creates a brand-new save dictionary. Everything persistent lives in this one dict, which is
# converted to JSON text on disk. Only released AND free (price 0) characters start owned, which at
# the moment is just the cat (the dog costs 0 but is not released).
def default_save(name="Player", character="cat"):
    """A brand-new save. Only released characters that cost 0 start unlocked."""
    return {
        "name": name,
        "character": character,
        "owned_chars": [k for k, c in CHARACTERS.items()
                        if c["released"] and c["price"] == 0],
        "coins": 0,
        "total_score": 0,
        "max_score": 0,
        "max_stage": 0,
        "games_played": 0,
        "total_kills": 0,
        "kills": {},          # monster key -> times killed
        "coins_spent": 0,
        "spells": [],         # monster keys whose killer spell is owned
        "claimed": [],        # achievement ids already claimed (no double rewards)
    }


# Returns the save as a dict, or None when there is no usable save.
# Steps: (1) no file -> None. (2) read & parse JSON; a damaged/unreadable file counts as "no
# save" instead of crashing. (3) start from a fresh default save and update() it with the file
# data: any field the OLD file lacks (added in a later game version) keeps its default, while
# everything stored in the file overrides the defaults - this keeps old saves compatible.
# (4) if the stored character no longer exists, fall back to the cat.
# Known edge case: if the file contains valid JSON that is not an object (for example a list),
# data.get(...) raises an AttributeError that is not caught here.
def load_save():
    """Return the saved game as a dict, or None if there is no (valid) save."""
    if not SAVE_FILE.exists():
        return None
    try:
        data = json.loads(SAVE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None                                  # unreadable file = no save
    save = default_save(data.get("name", "Player"), data.get("character", "cat"))
    save.update(data)       # keeps old saves working when new fields are added
    if save["character"] not in CHARACTERS:
        save["character"] = "cat"
    return save


# Safe saving: write the full text to a temporary file first, then tmp.replace(SAVE_FILE) swaps it
# in. The swap is atomic, so if the game or computer crashes mid-write, the old save is still intact
# instead of becoming a half-written corrupt file. indent=2 just makes the JSON human-readable.
def write_save(save):
    """Write to a temp file first, then swap it in, so a crash mid-write can
    never leave a half-written (corrupt) save file."""
    tmp = SAVE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(save, indent=2), encoding="utf-8")
    tmp.replace(SAVE_FILE)


# =============================================================================
# 8. KEYBOARD INPUT  (no Enter needed, works while animations run)
# =============================================================================
# Normally input() waits for Enter and blocks everything. Here we put the
# terminal into "cbreak" mode so single key presses arrive immediately, and we
# poll with a timeout so the screen can keep animating between key presses.
#
# `with Keys() as keys:` is a *context manager*: __enter__ runs when the block
# starts (switch the terminal mode) and __exit__ ALWAYS runs when it ends -
# even after an error - so your terminal is never left in a broken state.
#
# keys.get(timeout) returns: a one-character string (lowercase unless
# lower=False), or "up"/"down"/"left"/"right"/"esc", or None if nothing was
# pressed within `timeout` seconds.
# Two different implementations with the SAME interface (get / drain) are defined depending on the
# operating system, so the rest of the game does not care which one it is running on.
# os.name == "nt" means Windows, which uses the msvcrt module. Everything else (Linux/macOS) uses
# termios + select.
#
# Interface summary:
# keys.get(timeout, lower=True) -> a one-character string, or "up"/"down"/"left"/"right"/"esc",
# or None if nothing was pressed within `timeout` seconds.
# keys.drain() -> throw away any buffered key presses.
if os.name == "nt":
    # Windows-only module for reading single key presses directly from the console.
    import msvcrt

    # WINDOWS version. Using `with Keys() as keys:` guarantees __exit__ runs even if an error
    # occurs.
    # Windows needs no special terminal setup, so __enter__ and __exit__ do nothing here.
    class Keys:
        # Runs at the start of the `with` block. Returns the object itself so `as keys` receives it.
        def __enter__(self):
            return self

        # Runs at the end of the `with` block (even after errors). Nothing to restore on Windows.
        def __exit__(self, *args):
            pass

        # Poll for a key until `timeout` seconds pass.
        def get(self, timeout, lower=True):
            # Deadline: current time + timeout.
            end = time.time() + timeout
            while True:
                # kbhit() = "is a key waiting in the buffer?" (does not block). If yes, read it with
                # getwch().
                if msvcrt.kbhit():                       # is a key waiting?
                    # getwch() reads one wide character without echoing it to the screen.
                    ch = msvcrt.getwch()
                    # Ctrl+C arrives as the character \x03 here (raw reading bypasses normal Ctrl+C
                    # handling), so we
                    # raise KeyboardInterrupt ourselves; main() catches it and leaves quietly.
                    if ch == "\x03":                     # Ctrl+C
                        raise KeyboardInterrupt
                    # Arrow keys (and function keys) send TWO characters: a prefix (\x00 or \xe0)
                    # and a code. We read the
                    # second one right away and translate it: H=up, P=down, K=left, M=right. Unknown
                    # codes give None.
                    if ch in ("\x00", "\xe0"):           # arrow keys send 2 chars
                        return {"H": "up", "P": "down",
                                "K": "left", "M": "right"}.get(msvcrt.getwch())
                    # A lone \x1b character is the ESC key.
                    if ch == "\x1b":
                        return "esc"
                    # Normal key: return it in lowercase (so "Q" and "q" behave the same) unless
                    # lower=False was
                    # requested (used when typing the player name so capital letters are kept).
                    return ch.lower() if lower else ch
                # No key and the deadline has passed -> return None ("nothing was pressed").
                if time.time() >= end:
                    return None
                # Otherwise wait 10 ms and check again. Without this sleep the loop would spin at
                # 100 percent CPU.
                time.sleep(0.01)

        # Discard every key currently waiting in the buffer (used before a menu starts so stale key
        # presses made during an animation do not trigger something).
        def drain(self):
            """Throw away any keys pressed while we were busy."""
            while msvcrt.kbhit():
                msvcrt.getwch()
else:
    # LINUX / macOS version. Three modules:
    # select - wait until a file (here: the keyboard) has data, with a timeout
    # termios - read/write terminal settings
    # tty - convenience function setcbreak()
    import select
    import termios
    import tty

    # Unix version of the same class (same interface as the Windows one).
    class Keys:
        # Called when the `with` block starts. fd = the file descriptor number of the keyboard
        # (stdin).
        def __enter__(self):
            self.fd = sys.stdin.fileno()
            # Remember the CURRENT terminal settings so we can put them back at the end.
            self.old = termios.tcgetattr(self.fd)        # remember terminal settings
            # cbreak mode: key presses are delivered immediately (no waiting for Enter) and are not
            # echoed to
            # the screen. Ctrl+C still generates a KeyboardInterrupt in this mode.
            tty.setcbreak(self.fd)                       # keys arrive immediately, no echo
            return self

        # ALWAYS runs when the `with` block ends (even after a crash). Restores the saved terminal
        # settings
        # so the user's terminal is not left in a broken state.
        def __exit__(self, *args):
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)   # restore

        # Reads ONE logical character. We use os.read (raw bytes) instead of sys.stdin.read, because
        # Python's
        # buffered stdin would not cooperate with select(). Letters like "a-umlaut" are several
        # bytes in UTF-8,
        # so we must read all of them and decode together.
        def _read_char(self):
            """Read one character. Letters like a-umlaut are several bytes in
            UTF-8, so read the extra bytes too."""
            # Read the first byte. Empty result means end of input.
            b = os.read(self.fd, 1)
            if not b:
                return ""
            # The first byte of a UTF-8 character tells how many continuation bytes follow:
            # below 0xC0 -> plain ASCII (0 extra), 0xC0-0xDF -> 1 extra, 0xE0-0xEF -> 2 extra,
            # 0xF0 and above -> 3 extra. Then read that many more bytes and decode the whole thing
            # (errors="ignore" drops anything invalid instead of crashing).
            extra = 0 if b[0] < 0xC0 else 1 if b[0] < 0xE0 else 2 if b[0] < 0xF0 else 3
            if extra:
                b += os.read(self.fd, extra)
            return b.decode(errors="ignore")

        # Wait up to `timeout` seconds for a key.
        # select.select([fd], [], [], timeout) returns lists of ready files; [0] is the "ready to
        # read" list,
        # so an empty list (falsy) means nothing arrived -> return None.
        def get(self, timeout, lower=True):
            # select() waits until the keyboard has data or the timeout passes.
            # Nothing was typed within the timeout -> None.
            if not select.select([self.fd], [], [], timeout)[0]:
                return None
            # Something arrived: read it as one character.
            ch = self._read_char()
            # Arrow keys send an escape sequence like ESC [ A. A lone ESC key press sends just ESC.
            # To tell them
            # apart we wait 20 ms: if no more bytes follow it was the ESC key, else read 2 more
            # bytes and map
            # the sequence ("[A" up, "[B" down, "[D" left, "[C" right; "OA" etc. are the same keys
            # in
            # "application mode" that some terminals use). Unknown sequences return None.
            if ch == "\x1b":                             # start of an arrow key...
                if not select.select([self.fd], [], [], 0.02)[0]:
                    return "esc"                         # ...or just the ESC key
                seq = os.read(self.fd, 2).decode(errors="ignore")
                return {"[A": "up", "[B": "down", "[D": "left", "[C": "right",
                        "OA": "up", "OB": "down", "OD": "left", "OC": "right"}.get(seq)
            # Regular key: lowercase it unless lower=False (needed for typing names with capitals).
            return ch.lower() if lower else ch

        # Read and discard bytes until select() says the keyboard buffer is empty.
        def drain(self):
            while select.select([self.fd], [], [], 0)[0]:
                os.read(self.fd, 1)


# =============================================================================
# 8b. AUDIO  (needs the optional "pygame" package: pip install pygame)
# =============================================================================
# pygame.mixer can play several sounds at once, so the menu music keeps going
# while note sounds overlap. If pygame or the files are missing, every method
# below quietly does nothing and the game simply runs without sound.
# Absolute folder of this script (resolve() makes the path absolute and clean).
SCRIPT_DIR = Path(__file__).resolve().parent
# A tiny separate file that remembers whether sound is on or off between runs.
SETTINGS_FILE = SCRIPT_DIR / "settings.json"      # remembers the master volume switch
# Folders searched for sound files: first a "sounds" folder, then the script folder itself.
SOUND_DIRS = [SCRIPT_DIR / "sounds", SCRIPT_DIR]  # looked up in this order
# File names (without extension) of the 8 note sounds. Key "1" plays "do.wav", key "5" plays
# "sol.wav" ...
NOTE_SOUNDS = ["do", "re", "mi", "fa", "sol", "la", "si", "dohigh"]   # keys 1-8
# For each music mode a list of accepted file names, tried in order. The first one that exists
# is used. This is why "mainmenusongcut.wav" or "mainmenu.wav" etc. all work.
MUSIC_NAMES = {"menu": ["mainmenusongcut", "mainmenusong", "mainmenu"],
               "gameover": ["game_overcut", "gameovercut", "gameover", "game over", "game_over"]}


# The sound system. Every method quietly does nothing if pygame or the files are missing, so
# the game can run without sound. A single global instance (AUDIO) is created below the class.
class Audio:
    # Only sets up variables here (no pygame yet). Real initialisation happens in init().
    def __init__(self):
        # ready   - True after pygame's mixer started successfully.
        self.ready = False          # True once pygame + the mixer started
        # enabled - the master switch toggled in the Options screen (saved in settings.json).
        self.enabled = True         # the master volume switch (Options screen)
        # mode    - which background track SHOULD be playing: "menu" (looping song), "gameover"
        # (jingle once) or "none" (silence, used in battle).
        self.mode = "none"          # which track SHOULD play: "menu", "gameover", "none"
        # sounds  - dictionary: lowercase file name without extension -> loaded pygame Sound object.
        self.sounds = {}            # lower-case file name (no extension) -> Sound
        # problems - human-readable error messages shown at the bottom of the Options screen.
        self.problems = []          # shown in the Options screen if something is wrong
        # pg      - the pygame module itself, channel - the dedicated music channel (set in init()).
        self.pg = self.channel = None
        # pool    - list of channels used for note/enemy sounds, _rr - round-robin counter used when
        # all channels are busy.
        self.pool, self._rr = [], 0
        try:
            # Load the saved on/off setting. Reading may fail (file missing = OSError, bad JSON =
            # ValueError,
            # JSON not an object = AttributeError); in all those cases we silently keep the default
            # (True).
            self.enabled = bool(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("sound", True))
        except (OSError, ValueError, AttributeError):
            pass

    # Starts pygame's mixer and loads every .wav/.ogg file. Called once from main().
    def init(self):
        # Tell pygame not to print its "Hello from the pygame community" banner into our game
        # screen.
        # setdefault only sets it if the user has not already chosen a value.
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        try:
            # Imported HERE (not at the top of the file) so a missing pygame raises an error we can
            # catch
            # instead of breaking the whole program at start-up.
            import pygame
            # Must be called BEFORE mixer.init(). Parameters: 48000 Hz sample rate, -16 = signed
            # 16-bit samples,
            # 2 channels (stereo), 512 sample buffer. A small buffer means low latency, so the note
            # sound plays almost the instant the key is pressed.
            pygame.mixer.pre_init(48000, -16, 2, 512)    # small buffer = low latency
            # Actually open the audio device with the settings above.
            pygame.mixer.init()
            # Allow 24 sounds to play at the same time (the default is only 8).
            pygame.mixer.set_num_channels(24)
        # Any failure leaves self.ready False. ImportError gets a friendly "install pygame" message,
        # other errors (e.g. no audio device) show the raw message. Then init() stops - the game
        # continues silent.
        except Exception as e:
            self.problems.append("pygame missing - pip install pygame-ce"
                                 if isinstance(e, ImportError) else f"mixer error: {e}")
            return
        # Keep a reference to the module for use in the other methods.
        self.pg = pygame
        # Channel 0 = music / game over. Channels 1-23 = note and enemy sounds.
        # We pick channels ourselves instead of letting pygame choose.
        # We manage channels ourselves instead of letting pygame choose: channel 0 is RESERVED for
        # music
        # and the game-over jingle, channels 1-23 (self.pool) are for note and monster sounds. That
        # way a
        # flood of note sounds can never cut off the music.
        self.channel = pygame.mixer.Channel(0)
        # Channels 1..23 for short sound effects.
        self.pool = [pygame.mixer.Channel(i) for i in range(1, 24)]
        # Find all sound files. files maps lowercase file stem -> Path.
        files = {}
        # reversed() processes the script folder first and the "sounds" folder last. Because later
        # assignments overwrite earlier ones for the same file name, the "sounds" folder WINS if the
        # same
        # file exists in both.
        for d in reversed(SOUND_DIRS):                   # "sounds" folder wins
            if d.is_dir():
                for f in d.iterdir():
                    if f.suffix.lower() in (".wav", ".ogg"):
                        files[f.stem.lower()] = f
        # Load every file into memory as a pygame Sound. A broken file is reported in self.problems
        # but does not stop the others from loading.
        for stem, f in files.items():
            try:
                self.sounds[stem] = pygame.mixer.Sound(str(f))
            except Exception as e:
                self.problems.append(f"{f.name}: {e}")
        # The game-over jingle may start with silence, which would feel laggy. For the first file
        # that
        # exists, replace the loaded sound with a version whose leading silence is cut off
        # (_trimmed).
        # break = only handle the first matching name.
        for name in MUSIC_NAMES["gameover"]:             # cut the silence before the jingle
            if name in files:
                trimmed = self._trimmed(files[name])
                if trimmed:
                    self.sounds[name] = trimmed
                break
        # Everything worked: audio is usable now.
        self.ready = True

    # Loads a .wav file WITHOUT its leading silence so it starts the moment we play it.
    # threshold - a sample louder than 300 (out of 32767) counts as "sound", keep - how much silence
    # (seconds)
    # to keep before the first loud sample, so the attack is not clipped.
    # Returns None if the file cannot be handled; the caller then simply keeps the original sound.
    def _trimmed(self, path, threshold=300, keep=0.02):
        """Load a .wav without its leading silence (so it starts the instant we play it)."""
        try:
            # Python's built-in wav reader.
            import wave
            # Read the file's format (channels, bytes per sample, sample rate) and all of its raw
            # audio data.
            with wave.open(str(path)) as w:
                ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
                data = w.readframes(w.getnframes())
            # The mixer's current format is (frequency, format, channels). We can only build a Sound
            # from raw
            # bytes if the file has exactly the same format as the mixer: 16-bit, same rate, same
            # channel
            # count, mixer format -16. Otherwise give up (return None).
            init = self.pg.mixer.get_init()              # (frequency, format, channels)
            if width != 2 or not init or init[0] != rate or init[2] != ch or init[1] != -16:
                return None                              # format differs: keep the original
            # View the bytes as signed 16-bit integers ("h"). len//2*2 makes sure the byte count is
            # even.
            samples = memoryview(data[:len(data) // 2 * 2]).cast("h")
            # Index of the first sample whose absolute value exceeds the threshold (0 if none).
            # Dividing by the
            # channel count converts "sample index" to "frame index" (one frame = one sample per
            # channel).
            first = next((i for i, x in enumerate(samples) if abs(x) > threshold), 0) // ch
            # Start slightly before the first loud sample (keep seconds), converted back to a byte
            # offset:
            # frames * channels * 2 bytes per 16-bit sample.
            start = max(0, first - int(rate * keep)) * ch * 2
            # Build a new Sound from the bytes after the cut point.
            return self.pg.mixer.Sound(buffer=data[start:])
        except Exception:
            return None

    # Shut the audio device down at the end of the program.
    def close(self):
        if self.ready:
            self.pg.mixer.quit()
            self.ready = False

    # Builds the single status line shown at the bottom of the Options screen, so the player can see
    # at a glance whether the sound files were found.
    def status(self):
        """One short line for the Options screen."""
        if not self.ready:
            return "Sound OFF: " + (self.problems[0] if self.problems else "not started")
        # Every file the game expects: 8 notes + 3 enemy-death sounds.
        need = NOTE_SOUNDS + ["enemydead1", "enemydead2", "enemydead3"]
        # List of expected sounds that were not loaded.
        missing = [n for n in need if n not in self.sounds]
        # Music counts as present if at least one of the accepted file names was loaded.
        for mode in ("menu", "gameover"):
            if not any(n in self.sounds for n in MUSIC_NAMES[mode]):
                missing.append(mode + " music")
        if missing:
            return "Missing sound files: " + ", ".join(missing)
        if self.problems:
            return "Sound problem: " + self.problems[0]
        return "Sound OK - all files loaded"

    # Returns the Sound object for the current mode (first accepted name that was loaded), or None.
    def _track(self):
        for name in MUSIC_NAMES.get(self.mode, []):
            if name in self.sounds:
                return self.sounds[name]
        return None

    # Applies the current mode: stop everything, then start the right track (if sound is enabled).
    def _start(self):
        if not self.ready:
            return
        # Stops ALL channels first, so the previous music or leftover effects never overlap the new
        # track.
        self.pg.mixer.stop()                             # silence EVERYTHING first
        # Play on the music channel. loops=-1 means "repeat forever" (menu song); loops=0 means
        # "play
        # once" (game over jingle).
        snd = self._track()
        if self.enabled and snd:
            self.channel.play(snd, loops=-1 if self.mode == "menu" else 0)   # menu loops

    # Switch the background track. Asking for the mode that is already active does nothing, so the
    # menu song does not restart each time you open a new menu.
    def set_mode(self, mode):
        """'menu' = looping main menu song, 'gameover' = game over jingle once,
        'none' = silence (battle). Asking for the current mode again does nothing,
        so the song never restarts when you move between menus."""
        if mode != self.mode:
            self.mode = mode
            self._start()

    # Flip master volume on/off, save the new setting, then re-apply the mode (off = silence
    # everything,
    # on = resume the current track) and play a short beep so the player hears that it works.
    def toggle(self):
        self.enabled = not self.enabled
        try:
            SETTINGS_FILE.write_text(json.dumps({"sound": self.enabled}), encoding="utf-8")
        except OSError:
            pass
        self._start()                                    # off = stop all, on = resume current track
        if self.enabled:
            self.note(1)                                 # a little "beep" so you can hear it works

    # Play a sound effect by name on a free channel from the pool.
    def _play(self, name):
        snd = self.sounds.get(name)
        if not (self.ready and self.enabled and snd):
            return
        # First channel in the pool that is not busy; None if every channel is playing.
        ch = next((c for c in self.pool if not c.get_busy()), None)
        # All busy: steal one channel in round-robin order so the newest sound is never lost.
        if ch is None:                                   # all busy: reuse one (round robin)
            self._rr = (self._rr + 1) % len(self.pool)
            ch = self.pool[self._rr]
        # Reset the volume (a channel may have been changed earlier), then play the sound.
        ch.set_volume(1.0)
        ch.play(snd)

    # Play note n (1-8) = sound file NOTE_SOUNDS[n - 1].
    def note(self, n):
        self._play(NOTE_SOUNDS[n - 1])

    # Plays one of the available "enemydead" sounds picked at random.
    def enemy_dead(self):
        names = [n for n in ("enemydead1", "enemydead2", "enemydead3") if n in self.sounds]
        if names:
            self._play(random.choice(names))             # one at random


# The single global sound object used throughout the game (AUDIO.note(3), AUDIO.set_mode("menu"),
# ...).
AUDIO = Audio()

# Blocks until ANY key is pressed: clear stale keys first, then poll in 0.2 s steps until get()
# returns
# something other than None.
def wait_key(keys):
    """Block until any key is pressed."""
    keys.drain()
    while keys.get(0.2) is None:
        pass


# =============================================================================
# 9. DRAWING TOOLS
# =============================================================================
# A regular expression (regex) that matches ANSI color codes such as "\033[31m".
# re.sub(pattern, "", text) deletes every match, leaving only visible characters.
# We need this because a colored string is LONGER than what you see on screen,
# and all our padding/centering must be based on what you see.
# REGULAR EXPRESSION matching ANSI escape codes: ESC, "[", any digits/semicolons/question marks,
# then one letter (e.g. "\x1b[31m"). re.sub(pattern, "", text) deletes all matches, which gives the
# text as the user actually SEES it. We need this because a colored string is longer in memory than
# on screen, and all padding/centering must be calculated from the visible length.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# Visible length = length of the text with color codes removed.
def vlen(text):
    """Visible length: the length of the text without color codes."""
    return len(ANSI_RE.sub("", text))


# Add spaces on the right until the text is `width` VISIBLE characters wide (max(0, ...) prevents a
# negative count when the text is already longer).
def pad(text, width):
    """Pad with spaces on the right up to `width` visible characters."""
    return text + " " * max(0, width - vlen(text))


# Center ONE line inside `width` (default: inner frame width). Spare space is split left=total//2,
# right=the rest, so the line is always exactly `width` visible characters wide.
def center_text(text, width=INNER_W):
    total = max(0, width - vlen(text))
    return " " * (total // 2) + text + " " * (total - total // 2)


# Center a multi-line drawing as ONE block: compute the width of its widest line and add the same
# left
# indent to every line. Centering line by line would destroy the shape of the drawing.
def center_block(lines, width=INNER_W):
    """Center a multi-line drawing as ONE block (keeps its shape intact)."""
    w = max((vlen(l) for l in lines), default=0)
    indent = " " * max(0, (width - w) // 2)
    return [indent + l for l in lines]


# Place two blocks of lines next to each other. The left block is padded to its widest line (using
# vlen so colors are ignored), then `gap` spaces, then the right block. Missing rows (a shorter
# block) are treated as empty.
def side_by_side(left, right, gap=6):
    w = max((vlen(l) for l in left), default=0)
    out = []
    for i in range(max(len(left), len(right))):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        out.append(pad(l, w) + " " * gap + r)
    return out


# Draw a simple +---+ box around text lines. Width = widest line + 2 for spacing. It uses len(),
# not vlen(), so the lines inside must not contain color codes.
def box(lines):
    w = max(len(l) for l in lines) + 2
    return (["+" + "-" * w + "+"]
            + ["| " + l.ljust(w - 1) + "|" for l in lines]
            + ["+" + "-" * w + "+"])


# One menu entry. The selected entry is drawn in REVERSE video (inverted colors).
def menu_item(text, selected):
    """A menu entry; the selected one is drawn in inverted colors."""
    return (REVERSE + f" {text} " + RESET) if selected else f" {text} "


# Progress bar like [######----------]. `filled` = how many "#" (clamped at 0 if cur is negative).
# The filled part is colored, the empty part DIM; RESET closes the colors.
def bar(cur, total, width, color):
    filled = int(round(width * max(cur, 0) / total)) if total else 0
    return f"[{color}{'#' * filled}{DIM}{'-' * (width - filled)}{RESET}]"


# The big "TREBLEMAKER" header, colored yellow + bold, ready to put at the top of a screen.
def title_block():
    return [YELLOW + BOLD + line + RESET for line in TITLE]


# DRAWS ONE COMPLETE FRAME - the heart of the rendering system.
# How it avoids flicker: instead of clearing the whole screen (which blinks), we move the cursor to
# the top-left corner and paint the new frame over the old one:
# "\033[H" - cursor to row 1, column 1 (home), without erasing anything
# "\033[K" - erase from the cursor to the END of the current line (removes leftovers of the old
# frame)
# "\033[J" - erase everything below the cursor (removes leftover old rows)
# The whole frame is joined into ONE string and written with a single write + flush, so it appears
# at once.
def show(lines):
    """Draw a whole frame. The frame is centered in the window.

    "\\033[H" moves the cursor to the top-left WITHOUT clearing the screen;
    we overwrite the old frame line by line, and "\\033[K" erases whatever is
    left of each old line. That avoids the flicker you would get from clearing
    the whole screen every frame. "\\033[J" erases everything below the frame.
    """
    # Current terminal size (it can change while the game runs, so ask every frame).
    cols, rows = shutil.get_terminal_size()
    # Horizontal centering: the left margin so a frame of MENU_W columns is centered in the window.
    left = " " * max(0, (cols - MENU_W) // 2)
    # Vertical centering: number of empty rows above the frame.
    top = max(0, (rows - MENU_H) // 2)
    # Start with "cursor home", then `top` blank (but line-erasing) rows.
    out = ["\033[H"] + ["\033[K\n"] * top
    # Each frame row = left margin + the row + "erase rest of line". A newline is added after every
    # row
    # EXCEPT the last: if a frame fills the entire window height, a final newline would scroll the
    # terminal and shift everything up.
    for i, line in enumerate(lines):
        out.append(left + line + "\033[K")
        if i < len(lines) - 1:               # no newline after the last line,
            out.append("\n")                 # so a full-height frame can't scroll
    out.append("\033[J")
    # One single write for the whole frame, then flush so it is shown immediately.
    sys.stdout.write("".join(out))
    sys.stdout.flush()


# The bottom border line: dashes with a short hint text centered in them
# ("---- W/S: move | ENTER: select ----"). Without hint it is just a plain dash line.
def hint_border(hint=""):
    """A line of dashes with a short hint text in the middle."""
    if not hint:
        return "-" * MENU_W
    text = f" {hint} "
    total = MENU_W - len(text)
    return "-" * (total // 2) + text + "-" * (total - total // 2)


# Wrap content in the same border style as the title screen and return the finished frame
# (MENU_H rows). Steps: cut the content to INNER_H rows, fill missing rows with empty strings to
# reach
# exactly INNER_H, then add side bars "|" around each row (padded with pad(), so colored text
# lines up) plus the top and the bottom (hint) border: 1 + 31 + 1 = 33 rows.
# Note: a row wider than INNER_W is NOT cut and would push its right "|" out of line.
def framed(inner, hint=""):
    """Put lines inside the same dash-and-pipe frame the menu uses."""
    inner = list(inner)[:INNER_H]
    inner += [""] * (INNER_H - len(inner))               # fill up to full height
    rows = ["-" * MENU_W]
    rows += ["|" + pad(line, INNER_W) + "|" for line in inner]
    rows.append(hint_border(hint))
    return rows


# A GRID of single characters we can "paint" art onto, like a tiny image. Used for the falling intro
# and for the battle scene. Each cell holds exactly ONE visible character (possibly wrapped in color
# codes), so joining the cells of a row can never misalign, even with colors.
# Later paintings cover earlier ones (painter's algorithm), so draw order = layering.
class Canvas:
    """A grid of single characters we can 'paint' art onto (used for the intro
    and for the battle scene). Each cell holds ONE visible character, possibly
    wrapped in color codes, so joining the cells never breaks alignment."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.cells = [[" "] * w for _ in range(h)]

    # Paint ONE line of text at (row, col).
    # Spaces are normally transparent (the background shows through). With opaque=True the spaces
    # INSIDE the drawing erase the background, so solid sprites (cat, monsters) do not show the
    # mountains
    # through their gaps. Spaces BEFORE the first visible character (the indent) always stay
    # transparent.
    def put(self, row, col, text, color="", opaque=False):
        """Paint one line of text at (row, col).
        Spaces are normally see-through. With opaque=True the spaces *inside*
        the drawing erase the background, so sprites look solid."""
        # lead = number of leading spaces; text.rstrip() below removes trailing spaces.
        lead = len(text) - len(text.lstrip())
        # Go through the characters; i is the position inside the text, x the screen column.
        for i, ch in enumerate(text.rstrip()):
            x = col + i
            if not (0 <= row < self.h and 0 <= x < self.w):
                continue                                  # off-screen: skip
            # A space: only overwrite the background if opaque and the space is inside the drawing
            # (not leading).
            # Either way we do `continue` so the space is never drawn as colored text.
            if ch == " ":
                if opaque and i >= lead:
                    self.cells[row][x] = " "
                continue
            # A visible character: store it, wrapped with color + RESET if a color was given.
            # Off-screen
            # characters (negative column or beyond the edges) were skipped above, which clips
            # sprites cleanly.
            self.cells[row][x] = f"{color}{ch}{RESET}" if color else ch

    # Paint a multi-line drawing: line number i goes one row below line i-1.
    def art(self, lines, row, col, color="", opaque=False):
        for i, line in enumerate(lines):
            self.put(row + i, col, line, color, opaque)

    # Convert the grid into a list of strings (one per row) that show() can print.
    def lines(self):
        return ["".join(r) for r in self.cells]


# =============================================================================
# 10. OPENING ANIMATION + TITLE MENU
# =============================================================================
# Finds where the landing pose has to be placed so it exactly covers the cat that is already
# drawn in the MENU art. Returns (column offset, menu row of the cat's bottom line).
def landing_spot():
    """Where the landing pose sits inside the menu: (columns from the left,
    bottom row). We find the cat inside the MENU drawing so the falling cat
    lands exactly on top of it."""
    # The landing pose as a list of rows.
    land = art_lines(LANDING)
    # The bottom row of the cat in the menu contains the text "n| (". When we find it, the cat's
    # FIRST row is len(land) - 1 rows higher.
    for row, line in enumerate(MENU_LINES):
        # Found the cat's bottom line.
        if "n| (" in line:                       # bottom line of the cat in the menu
            # The menu row that holds the first line of the cat.
            top_line = MENU_LINES[row - len(land) + 1]
            # The column of the "_ /|" head-shape in the menu minus its column inside LANDING = the
            # horizontal
            # offset needed to line the landing pose up with the menu's cat.
            return top_line.index("_ /|") - land[0].index("_ /|"), row
    # Safety fallback if the menu art has no cat.
    return 0, MENU_H - 2                         # fallback if the menu has no cat


# The game needs a window of at least MENU_W x MENU_H. Loop until the window is big enough
# (the player can resize it while we wait). Returns False if Q was pressed.
def ensure_size(keys):
    """The game needs MENU_W x MENU_H. Wait until the window is big enough.
    Returns False if the player pressed Q instead."""
    # Remember the last size we showed a message for, to avoid redrawing the message every 0.3 s.
    shown = None
    while True:
        # Ask for the current window size.
        size = shutil.get_terminal_size()
        # Big enough -> start the game.
        if size.columns >= MENU_W and size.lines >= MENU_H:
            return True
        # Only rewrite the "too small" message when the size changed. "\033[H\033[J" = cursor home +
        # clear
        # the screen.
        if (size.columns, size.lines) != shown:  # only redraw when the size changes
            shown = (size.columns, size.lines)
            sys.stdout.write(
                "\033[H\033[J"
                f"  The window is too small ({size.columns} x {size.lines}).\n"
                f"  Please make it at least {MENU_W} x {MENU_H}: "
                "maximize the window or make the text smaller.\n\n"
                "  [Q] quit\n")
            sys.stdout.flush()
        # Wait 0.3 s for a key; Q quits (function returns False and main() exits).
        if keys.get(0.3) == "q":
            return False


# The opening animation: the cat falls from the top and lands on exactly the spot where the cat
# sits on the title screen. Pressing any key skips it.
def falling_intro(keys):
    """The cat slowly falls and lands exactly where it sits in the menu.
    Press any key to skip it."""
    # dx = horizontal offset, floor = menu row of the landed cat's bottom line.
    dx, floor = landing_spot()                   # floor = menu row of the cat's bottom line
    # Heights (rows) of the falling sprite and the landing pose.
    fall_h = len(art_lines(FALL_1))
    land_h = len(art_lines(LANDING))
    # The highest y at which the falling cat's TOP row may be so that its bottom lands on `floor`.
    last_top = max(0, floor - fall_h + 1)
    # One loop pass = one row of falling. y is the row of the cat's top edge.
    for y in range(last_top + 1):
        # A blank full-size canvas for this frame (no border during the fall, the border appears
        # with the menu).
        cv = Canvas(MENU_W, MENU_H)
        # y % 4 cycles through the four fall frames so the tail waves while the cat drops.
        cv.art(art_lines(FALL_FRAMES[y % len(FALL_FRAMES)]), y, dx)   # cycles the 4 frames
        show(cv.lines())
        # This single call is BOTH the frame delay AND the "skip" detector: it waits FALL_DELAY
        # seconds
        # and returns a value early if a key was pressed.
        if keys.get(FALL_DELAY):                 # waits FALL_DELAY, returns early on a key
            # Skipped by a key press: start the menu music right now and leave the function.
            AUDIO.set_mode("menu")               # skipped: start the music right away
            return
    # The fall finished by itself: the music starts at the moment of landing.
    AUDIO.set_mode("menu")                       # the cat has finished falling: music starts
    # Draw the final landing pose at the same place where the menu's cat is, hold it for a moment.
    cv = Canvas(MENU_W, MENU_H)
    cv.art(art_lines(LANDING), floor - land_h + 1, dx)
    show(cv.lines())
    # Hold the landing picture for LANDING_SECONDS (or until a key is pressed).
    keys.get(LANDING_SECONDS)


# TITLE_ITEMS maps a menu id to the exact text that is already drawn in the MENU art. We locate
# these texts in the art to know on which row each button is.
# The clickable labels that are already drawn inside the MENU art.
TITLE_ITEMS = {"continue": ">CONTINUE<", "new": ">NEW GAME<",
               "options": ">OPTIONS<", "tutorial": ">TUTORIAL<"}


# Prepares the title screen for drawing:
# (1) color the big title, (2) erase "CONTINUE" when no save exists, (3) highlight the selected
# entry.
# `available` = ids that are shown, `selected` = the highlighted id.
def render_title(available, selected):
    """Take the menu drawing and (1) color the title, (2) erase 'CONTINUE' if
    there is no save, (3) highlight the selected entry."""
    # Work on a COPY so the original menu art is never modified.
    lines = list(MENU_LINES)
    # Rows 3-10 are the title letters: yellow+bold between the two "|" border characters.
    for r in range(3, 11):
        lines[r] = "|" + YELLOW + BOLD + lines[r][1:-1] + RESET + "|"
    # For each button label: find the row containing it...
    for key, label in TITLE_ITEMS.items():
        # next(generator, default): the first line containing the label, else None
        # next(generator, None) returns the first row whose text contains the label, or None.
        row = next((i for i, l in enumerate(lines) if label in l), None)
        if row is None:
            continue
        # ...not available (e.g. CONTINUE without a save): overwrite the label with spaces so it
        # disappears
        # (the other buttons stay where they are)...
        if key not in available:
            lines[row] = lines[row].replace(label, " " * len(label))
        # ...the selected button: wrap the label in REVERSE so it is shown inverted.
        elif key == selected:
            lines[row] = lines[row].replace(label, REVERSE + label + RESET)
    # Replace the last border row with one that contains the hint text.
    lines[-1] = hint_border("W/S or arrows: move | ENTER: select | Q: quit")
    return lines


# THE GENERIC "PICK ONE" LOOP USED BY EVERY MENU.
# draw(sel) - a function that paints the screen with entry `sel` highlighted.
# cols      - 1 for a vertical list, 3 for a 3-wide grid (then left/right also move).
# start     - which entry is highlighted at first (so you return to where you were).
# animated  - if True, redraw every 0.1 s even without input (for screens with a moving cat).
# Returns the chosen index (ENTER) or None (Q / ESC).
def choose(keys, count, draw, cols=1, start=0, animated=False):
    """Generic 'pick one entry' loop used by EVERY menu in the game.

    draw(sel)  - a function that paints the screen for the highlighted entry.
    cols       - 1 for a list, 3 for a 3-wide grid (then left/right also work).
    animated   - redraw every 0.1 s (for screens with a moving cat).
    Returns the chosen index, or None if the player pressed Q / ESC.
    """
    # Currently highlighted entry.
    sel = start
    # Paint the first frame.
    draw(sel)
    # Throw away key presses made during the previous screen's animation so they do not act here.
    keys.drain()
    while True:
        # Wait up to 0.1 s for a key; 10 redraws per second for animated screens.
        key = keys.get(0.1)
        # Nothing pressed: redraw only if the screen is animated, then wait again.
        if key is None:
            if animated:
                draw(sel)
            continue
        # Back/cancel.
        if key in ("q", "esc"):
            return None
        # Enter key: Windows sends "\r", Linux/macOS terminals usually send "\n".
        if key in ("\r", "\n"):
            return sel
        # .get(key, 0) looks the key up in the dictionary; 0 if it isn't there.
        # Dictionary lookup of how far the selection moves: up/W = one row back (-cols entries),
        # down/S = one
        # row forward. In a one-column list that is -1/+1. .get(key, 0) returns 0 for any other key.
        delta = {"up": -cols, "w": -cols, "down": cols, "s": cols}.get(key, 0)
        # In a grid, left/A and right/D move by one entry.
        if cols > 1 and not delta:
            delta = {"left": -1, "a": -1, "right": 1, "d": 1}.get(key, 0)
        # Only act if a movement key was pressed.
        if delta:
            # Proposed new position.
            new = sel + delta
            if 0 <= new < count:
                sel = new
            # Inside the valid range -> accept. Outside: lists WRAP AROUND (modulo, so -1 becomes
            # the last
            # entry), while grids simply ignore the move. We redraw after any movement key.
            elif cols == 1:
                sel = new % count          # lists wrap around (top <-> bottom)
            draw(sel)


# A yes/no popup. Draws a question in the middle of an empty frame and returns True only if the
# player presses Y; N, Q, ESC or Enter all mean "no" (the safe answer).
def confirm(keys, question):
    """Yes/no popup. Returns True only if the player presses Y."""
    inner = ([""] * 11 + [center_text(YELLOW + BOLD + question + RESET), "",
                          center_text("[Y] yes     [N] no")])
    show(framed(inner))
    keys.drain()
    while True:
        key = keys.get(0.2)
        if key == "y":
            return True
        if key in ("n", "q", "esc", "\r", "\n"):
            return False


# =============================================================================
# 11. MENU SCREENS
# =============================================================================
# Lays out cells (characters) in a grid. Each cell is a dict with: art, label, sub (small text
# below), color and selected (highlight). `cols` cells per row, each `cell_w` characters wide.
def grid(cells, cols, cell_w):
    """Lay out characters in rows of `cols`. Each cell is a dict with the keys
    art, label, sub, color and selected."""
    # Output lines.
    lines = []
    for r in range(0, len(cells), cols):
        row = cells[r:r + cols]
        for y in range(ART_H):
            line = ""
            for c in row:
                # BOTTOM-ALIGN the art: shorter sprites get empty rows on top, so all characters
                # stand on the same
                # baseline. idx is the art row to print on grid row y (negative = nothing to draw
                # there).
                idx = y - (ART_H - len(c["art"]))        # bottom-align the art
                txt = c["art"][idx] if 0 <= idx < len(c["art"]) else ""
                line += pad(c["color"] + txt + RESET if txt else "", cell_w)
            lines.append(line)
        # After the art: the label row (the selected one inverted), then the sub text row, then a
        # blank row.
        lines.append("".join(pad(REVERSE + c["label"] + RESET if c["selected"]
                                 else c["label"], cell_w) for c in row))
        lines.append("".join(pad(c["sub"], cell_w) for c in row))
        lines.append("")
    return lines


# NEW GAME: pick a character, then type a name. Returns the new save dict, or None if the
# player backed out.
def create_character(keys):
    """New game: pick a character, then type a name. Returns the new save, or
    None if the player backed out."""
    # Fixed order of the characters (dictionary keys). The selected index maps to this list.
    order = list(CHARACTERS)
    # msg = a line of feedback text shown on the screen; sel = highlighted entry.
    msg, sel = "", 0
    while True:
        # Defined inside the loop so it can use the current `msg`. Builds one cell per character;
        # locked
        # characters are dimmed and labeled "== locked ==".
        def draw(s):
            # List of cell dicts for grid().
            cells = []
            for i, k in enumerate(order):
                c = CHARACTERS[k]
                cells.append({"art": c["idle"][0],
                              "label": f" {i + 1}. {c['name']} " if c["released"] else " == locked == ",
                              "sub": "", "color": "" if c["released"] else DIM,
                              "selected": i == s})
            # The screen: heading + grid + feedback message, placed in the frame.
            inner = ["", center_text(YELLOW + BOLD + "= SELECT YOUR CHARACTER =" + RESET),
                     center_text("Choose who will fight for you"), ""]
            inner += ["  " + l for l in grid(cells, 3, 32)]
            inner.append(center_text(RED + msg + RESET))
            show(framed(inner, "arrows: move | ENTER: pick | Q: back"))

        # Let the player pick with a 3-wide grid; returns None if they pressed Q/ESC (-> back to the
        # title).
        sel = choose(keys, len(order), draw, cols=3, start=sel)
        if sel is None:
            return None
        # Convert the chosen index back into a character id.
        key = order[sel]
        # Unreleased characters cannot be chosen: show a message and loop to ask again.
        if not CHARACTERS[key]["released"]:
            msg = "That character is locked for now."
            continue
        # Ask for the player's name. If they pressed ESC there (None), clear the message and go back
        # to the grid.
        name = ask_name(keys, key)
        if name is None:
            msg = ""
            continue
        # Build a fresh save with the chosen name and character...
        save = default_save(name, key)
        # ...and write it to disk. This is where savegame.json is created for the first time.
        write_save(save)               # <- this is where savegame.json is created
        return save


# Text input without input(): in cbreak mode we collect key presses ourselves. While you type, the
# chosen character idles on screen and a cursor blinks.
def ask_name(keys, char_key):
    """Type a name letter by letter (we can't use input() in cbreak mode, so we
    collect the key presses ourselves). The chosen cat idles while you type."""
    # The name typed so far.
    name = ""
    # Character definition (for its idle art).
    char = CHARACTERS[char_key]
    while True:
        # Animation loop: each pass recalculates the idle pose and the cursor from the clock.
        now = time.time()
        # Pose index = int(now / 1 s) % number of poses -> changes every IDLE_SWITCH_SECONDS.
        idle = char["idle"][int(now / IDLE_SWITCH_SECONDS) % len(char["idle"])]
        # Blinking cursor: shown on even half-seconds and hidden on odd ones.
        cursor = "_" if int(now * 2) % 2 == 0 else " "        # blinking cursor
        # Screen content: title, character, prompt and the name with the cursor.
        inner = [""] + title_block() + [""] + center_block(idle) + [
            "", center_text("Input your name:"),
            center_text(BOLD + name + cursor + RESET)]
        show(framed(inner, "type a name | ENTER: confirm | ESC: back"))
        # Wait 0.1 s for a key (lower=False so capital letters are kept).
        key = keys.get(0.1, lower=False)                  # lower=False keeps capitals
        if key is None:
            continue
        # Enter confirms - but only for a non-empty name (spaces alone do not count). Returns it
        # with
        # surrounding spaces removed.
        if key in ("\r", "\n") and name.strip():
            return name.strip()
        # ESC cancels (None).
        if key == "esc":
            return None
        # Backspace: "\x7f" on Linux/macOS, "\b" on Windows. Remove the last character.
        if key in ("\x7f", "\b"):                         # backspace
            name = name[:-1]
        # Any printable single character is added, up to 12 characters. len(key) == 1 filters out
        # names
        # like "up" or "esc" that mean special keys.
        elif len(key) == 1 and key.isprintable() and len(name) < 12:
            name += key


# The hub (main menu after you pick a save). The index of each entry corresponds to the
# actions list in hub().
HUB_ITEMS = ["Begin Battle", "Achievements", "Customize character",
             "Purchase Spells", "Save & back to title"]


# Draws the hub: character + stats on the left, menu list on the right. Called repeatedly (animated)
# so the cat keeps flipping between idle poses.
def draw_hub(save, sel):
    now = time.time()
    char = CHARACTERS[save["character"]]
    idle = char["idle"][int(now / IDLE_SWITCH_SECONDS) % len(char["idle"])]
    # Left column: the cat, her name in bold, and a stats box.
    left = list(idle) + ["", BOLD + save["name"] + RESET, ""]
    left += box([f"Level:      {level_of(save)}", f"Max score:  {save['max_score']}",
                 f"Best stage: {save['max_stage']}",
                 f"Coins:      {save['coins']}"])
    # Right column: each menu entry followed by a blank line; the selected one is inverted.
    right = [""]
    for i, text in enumerate(HUB_ITEMS):
        right += [menu_item(text, i == sel), ""]
    # Put both columns next to each other (gap 16) and indent them 10 columns.
    body = ["          " + l for l in side_by_side(left, right, gap=16)]
    show(framed([""] + title_block() + [""] + body,
                "W/S or arrows: move | ENTER: select | Q: save & back"))


# The hub loop: show the menu, run what the player picks, save, repeat.
def hub(keys, save):
    """The 4-option screen: battle, achievements, customize, spells."""
    sel = 0
    # Functions in the SAME ORDER as HUB_ITEMS (0-3). They all take (keys, save) so they can be
    # called
    # the same way.
    actions = [battle, achievements_menu, customize_menu, spells_menu]
    while True:
        # animated=True so the idle cat keeps moving. start=sel returns the highlight to where it
        # was.
        sel = choose(keys, len(HUB_ITEMS), lambda s: draw_hub(save, s),
                     start=sel, animated=True)
        # Q or "Save & back to title" (entry 4): write the save and return to the title screen.
        if sel is None or sel == 4:
            write_save(save)
            return
        # Run the chosen screen (battle, achievements, ...). The save is written afterwards so
        # progress
        # is never lost.
        actions[sel](keys, save)
        write_save(save)


# Lists all achievements; ENTER on a completed one claims its coin reward (only once).
def achievements_menu(keys, save):
    msg, sel = "", 0
    while True:
        def draw(s):
            inner = ["", center_text(YELLOW + BOLD + "= ACHIEVEMENTS =" + RESET),
                     center_text(f"Coins: {save['coins']}"), ""]
            for i, (aid, name, desc, reward, check) in enumerate(ACHIEVEMENTS):
                # claimed = already collected; ready = not yet collected but the condition is met
                # (check(save)).
                claimed = aid in save["claimed"]
                ready = (not claimed) and check(save)
                # Status text for each state.
                status = "claimed" if claimed else "READY - claim!" if ready else "in progress"
                # f-string alignment: {i + 1:>2} right-aligns in 2 characters, {name:<16}
                # left-aligns in 16, so the
                # columns line up like a table.
                row = f" {i + 1:>2}. {name:<16} {desc:<30} +{reward:<4}coins  {status:<14}"
                # Row color via chained conditional expressions: selected = inverted, claimed = dim,
                # ready = green,
                # otherwise normal.
                color = REVERSE if i == s else DIM if claimed else GREEN if ready else ""
                inner.append("   " + color + row + RESET)
            inner += ["", "   " + msg]
            show(framed(inner, "W/S or arrows: move | ENTER: claim | Q: back"))

        # Pick a row; Q goes back.
        sel = choose(keys, len(ACHIEVEMENTS), draw, start=sel)
        if sel is None:
            return
        # Unpack the tuple of the chosen achievement.
        aid, name, desc, reward, check = ACHIEVEMENTS[sel]
        # Three cases: already claimed, not completed yet, or claimable.
        if aid in save["claimed"]:
            msg = f"'{name}' was already claimed."
        elif not check(save):
            msg = f"{RED}You haven't completed '{name}' yet.{RESET}"
        else:
            # Record the claim FIRST (so it cannot be claimed twice), then add the coins and save.
            save["claimed"].append(aid)          # recorded, so it can't be claimed twice
            save["coins"] += reward
            write_save(save)
            msg = f"{GREEN}Claimed '{name}'! +{reward} coins.{RESET}"


# Equip, unlock (free by level) or buy characters.
def customize_menu(keys, save):
    order = list(CHARACTERS)
    msg, sel = "", 0
    while True:
        def draw(s):
            cells = []
            for i, k in enumerate(order):
                c = CHARACTERS[k]
                # Per character: equipped / owned / coming soon / price info. Colors: green =
                # equipped, dim = not owned.
                owned = k in save["owned_chars"]
                # The sub-text under each character depends on the state.
                if k == save["character"]:
                    sub, color = "EQUIPPED", GREEN
                elif owned:
                    sub, color = "owned - ENTER to equip", ""
                elif not c["released"]:
                    sub, color = "coming soon", DIM
                else:
                    sub, color = f"{c['price']} coins or Lv {c['level']}", DIM
                cells.append({"art": c["idle"][0], "label": f" {i + 1}. {c['name']} ",
                              "sub": sub, "color": color, "selected": i == s})
            inner = ["", center_text(YELLOW + BOLD + "= CUSTOMIZE CHARACTER =" + RESET),
                     center_text(f"Coins: {save['coins']}    Level: {level_of(save)}"), ""]
            inner += ["  " + l for l in grid(cells, 3, 32)]
            inner.append("   " + msg)
            show(framed(inner, "arrows: move | ENTER: equip / unlock | Q: back"))

        # Grid selection.
        sel = choose(keys, len(order), draw, cols=3, start=sel)
        if sel is None:
            return
        # The chosen character id and its data.
        k = order[sel]
        c = CHARACTERS[k]
        # DECISION ORDER on ENTER: (1) not released -> locked; (2) already owned -> equip; (3)
        # player's level
        # is high enough -> unlock for free and equip; (4) enough coins -> pay, record the spending
        # and equip;
        # (5) otherwise explain what is missing.
        if not c["released"]:
            msg = f"{RED}{c['name']} is locked for now.{RESET}"
        # Owned -> just equip.
        elif k in save["owned_chars"]:
            save["character"] = k
            msg = f"{GREEN}{c['name']} equipped!{RESET}"
        # Level high enough -> free unlock.
        elif level_of(save) >= c["level"]:
            save["owned_chars"].append(k)
            save["character"] = k
            msg = f"{GREEN}Level {c['level']} reached - {c['name']} unlocked for free!{RESET}"
        # Enough coins -> buy (coins_spent is tracked for the "Shopper" achievements).
        elif save["coins"] >= c["price"]:
            save["coins"] -= c["price"]
            save["coins_spent"] += c["price"]
            save["owned_chars"].append(k)
            save["character"] = k
            msg = f"{GREEN}Bought {c['name']} for {c['price']} coins!{RESET}"
        else:
            msg = (f"{RED}Not enough coins{RESET} ({c['price']} needed) "
                   f"and you are below level {c['level']}.")
        write_save(save)


# The Spell Book: buy killer spells. An owned spell kills its monster instantly when its note combo
# is played in battle.
def spells_menu(keys, save):
    # One spell per monster, in the order they are defined.
    order = list(MONSTERS)
    msg, sel = "", 0
    while True:
        def draw(s):
            left = []
            for i, k in enumerate(order):
                m = MONSTERS[k]
                # Left list: each spell with its price or OWNED, and underneath its note combo as
                # names AND key numbers.
                owned = k in save["spells"]
                status = "OWNED" if owned else f"{m['price']} coins"
                first = f" {i + 1}. {m['spell']:<18}{status:>10} "
                # Combo shown as note names (DO RE MI) and as the number keys to press (123).
                cast = " ".join(NOTES[n - 1] for n in m["cast"])
                keys_txt = "".join(str(n) for n in m["cast"])
                left += [(REVERSE if i == s else GREEN if owned else "") + first + RESET,
                         f"{DIM}      kills {m['name']:<9} cast: {cast}  ({keys_txt}){RESET}", ""]
            # Right side: a preview of the monster belonging to the HIGHLIGHTED spell (art, hp,
            # reward, first stage).
            m = MONSTERS[order[s]]
            right = [BOLD + m["name"] + RESET, ""] + list(m["art"]) + [
                "", f"HP: {m['hp']}   Reward: {m['coins']} coins",
                f"Appears from stage {m['min_stage'] + 1}"]
            # Both columns next to each other.
            body = ["   " + l for l in side_by_side(left, right, gap=6)]
            inner = ["", center_text(YELLOW + BOLD + "= SPELL BOOK =" + RESET),
                     center_text(f"Coins: {save['coins']}   -   an owned spell kills its monster instantly"),
                     ""] + body + ["", "   " + msg]
            show(framed(inner, "W/S or arrows: move | ENTER: buy | Q: back"))

        # Single-column list selection.
        sel = choose(keys, len(order), draw, start=sel)
        if sel is None:
            return
        k = order[sel]
        m = MONSTERS[k]
        # Buying: already owned -> message; too poor -> message; else pay, record spending, add to
        # owned spells, save.
        if k in save["spells"]:
            msg = f"You already know '{m['spell']}'."
        # Not enough coins.
        elif save["coins"] < m["price"]:
            msg = f"{RED}Not enough coins.{RESET} '{m['spell']}' costs {m['price']}."
        else:
            save["coins"] -= m["price"]
            save["coins_spent"] += m["price"]
            save["spells"].append(k)
            write_save(save)
            msg = f"{GREEN}Learned '{m['spell']}'!{RESET}"


# A static help text. It reuses cells_rows() (from the battle section) to show the note names and
# the number keys exactly as they appear in battle.
def tutorial_screen(keys):
    text = [
        "", center_text(YELLOW + BOLD + "= HOW TO PLAY =" + RESET), "",
        "   * Choose 'Begin Battle'. A monster walks toward your cat.",
        "   * Above you see the notes it takes to hurt it. Press the matching",
        "     number keys (1-8) - your cat pushes each note out at the monster:", "",
    ]
    # Shows the 8 notes with their keys.
    text += cells_rows([1, 2, 3, 4, 5, 6, 7, 8])
    text += [
        "",
        "   * A finished sequence deals 1 damage. A wrong note = start the sequence again.",
        "   * Defeat the monster before the time bar runs out, or your cat is toast!",
        "   * Every win gives coins + score. The next monster is harder:",
        "     longer sequences, less time, tougher monsters.",
        "   * Buy killer spells in the Spell Book: a short combo that kills that",
        "     monster type instantly.",
        "   * Spend coins on new characters, or reach their level to get them free.",
        "   * Claim achievements for bonus coins (each only once).",
    ]
    # Draw the page, then wait for any key.
    show(framed(text, "press any key"))
    wait_key(keys)


# Options: toggle sound, delete the save, or go back.
def options_screen(keys):
    msg, sel = "", 0
    while True:
        def draw(s):
            # Entries of the list: 0 = volume, 1 = delete save, 2 = back.
            items = [f"Master volume: {'ON ' if AUDIO.enabled else 'OFF'}",
                     "Delete saved game", "Back"]
            inner = ["", center_text(YELLOW + BOLD + "= OPTIONS =" + RESET), "", ""]
            for i, t in enumerate(items):
                inner.append(center_text(menu_item(t, i == s)))
                inner.append("")
            inner.append(center_text(msg))
            inner += ["", center_text(DIM + AUDIO.status() + RESET)]
            show(framed(inner, "W/S or arrows: move | ENTER: select | Q: back"))

        # Returns None for Q, 2 for Back; both leave the screen.
        sel = choose(keys, 3, draw, start=sel)
        if sel is None or sel == 2:
            return
        # Entry 0: toggle audio (only possible if audio initialised successfully); `continue` jumps
        # back to
        # the top of the loop so the delete code below is NOT run.
        if sel == 0:
            if not AUDIO.ready:
                msg = RED + "Sound unavailable - see the line below" + RESET
            else:
                AUDIO.toggle()
                msg = ""
            continue
        # Entry 1: delete the save, but only after a Y/N confirmation; unlink() removes the file.
        if SAVE_FILE.exists():
            if confirm(keys, "Delete your saved game for good?"):
                SAVE_FILE.unlink()                           # deletes the file
                msg = GREEN + "Save deleted." + RESET
            else:
                msg = ""
        else:
            msg = "There is no saved game."


# =============================================================================
# 12. BATTLE
# =============================================================================
# ---- scene background (made once, with a FIXED random seed) -----------------
# random.Random(7) is our own private random generator. Because the seed (7)
# is fixed, it makes the same "random" stars/ground every run - and they don't
# flicker between frames.
# THE BATTLE BACKGROUND. It is created ONCE at import time with a FIXED random seed, so the stars
# and the ground look the same on every run and never flicker between frames.
# SCENE_H = rows of the scene; GROUND_ROW = the row of the grass line (bottom row).
SCENE_H = 12
GROUND_ROW = SCENE_H - 1
# Our own private random generator with seed 7. Same seed -> same "random" sequence each time, and
# it does not disturb the global random used for monsters.
_rng = random.Random(7)
# 22 stars: (x position, row 0 or 1, drift speed in columns/second).
STARS = [(_rng.randrange(INNER_W), _rng.randrange(2), _rng.choice([1.5, 3.0, 5.0]))
         for _ in range(22)]                       # (x, row, speed)
# Two clouds: (art, starting x, speed).
CLOUDS = [(CLOUD, 10, 2.5), (CLOUD, 55, 1.5)]      # (art, start x, speed)
# Tile width = longest row of the mountain tile.
_TW = max(len(r) for r in _TILE)
# Pad each tile row to the full tile width, repeat it enough times to cover the screen, then cut to
# exactly INNER_W characters.
MOUNTAINS = [(r.ljust(_TW) * (INNER_W // _TW + 1))[:INNER_W] for r in _TILE]
# The grass line: a random mix of ground characters; "_" appears five times in the choice string,
# so it is the most common.
GROUND = "".join(_rng.choice("_____-.^'") for _ in range(INNER_W))


# Paints ONE battle picture and returns it as text lines. Layers, back to front:
# stars -> clouds -> mountains -> ground -> sprites (opaque, solid) -> overlays (see-through
# effects).
# sprites  = [(art lines, column, color, lift)]  solid characters standing on the ground
# overlays = [(art lines, column, row, color)]   transparent effects painted on top
# `now` (the clock) drives all background motion, so no state has to be stored.
def render_scene(now, sprites, overlays):
    """Paint one battle picture and return it as a list of text lines.

    sprites  = [(art lines, column, color, lift)]   solid things on the ground
    overlays = [(art lines, column, row, color)]    see-through effects on top
    """
    # Empty canvas the size of the scene.
    cv = Canvas(INNER_W, SCENE_H)
    # Stars drift left: position = (x - now * speed) wrapped by modulo, so they re-enter on the
    # right.
    for x, row, speed in STARS:                          # drifting, twinkling stars
        # Twinkle: the star is "*" one quarter of the time (changes every half second, offset by x
        # so they
        # do not all blink together), otherwise ".".
        ch = "*" if int(now * 2 + x) % 4 == 0 else "."
        cv.put(row, int(x - now * speed) % INNER_W, ch, DIM)
    # Same idea for clouds; the "+ 10 ... - 8" lets a cloud fully leave the screen before it
    # re-enters.
    for cloud, x0, speed in CLOUDS:                      # clouds scrolling left
        cv.art(cloud, 0, int(x0 - now * speed) % (INNER_W + 10) - 8, DIM)
    # Mountains occupy the 3 rows above the ground line.
    for i, row in enumerate(MOUNTAINS):
        cv.put(SCENE_H - 4 + i, 0, row, DIM)
    # Ground line in green.
    cv.put(GROUND_ROW, 0, GROUND, GREEN)
    # Sprites stand ON the ground: top row = GROUND_ROW - height - lift. `lift` raises a sprite
    # (used for
    # the monster's little hops). opaque=True makes them solid.
    for lines, col, color, lift in sprites:              # feet on the ground
        cv.art(lines, GROUND_ROW - len(lines) - lift, col, color, opaque=True)
    # Effects and projectiles are painted last, so they appear in front of everything else.
    for lines, col, row, color in overlays:
        cv.art(lines, row, col, color)
    return cv.lines()


# A little animation = dictionary: `frames` are shown one after another, each for `dur` seconds,
# with
# matching `colors`; (col, row) is the CENTER; t0 is the start time; `rise` makes it float upward
# (rows per second).
def make_effect(frames, colors, dur, col, row, now, rise=0):
    """An effect = a little animation: `frames` are shown one after another,
    each lasting `dur` seconds. `rise` makes it float upward."""
    return {"frames": frames, "colors": colors, "dur": dur,
            "col": col, "row": row, "t0": now, "rise": rise}


# What should be on screen right now for this effect? Returns an overlay tuple, or None when the
# animation has finished (the caller then removes it).
def effect_overlay(fx, now):
    """Which frame of the effect should be on screen right now? None = finished."""
    # Seconds since the effect started.
    age = now - fx["t0"]
    # Current frame number = age / seconds per frame.
    idx = int(age / fx["dur"])
    # Past the last frame -> finished.
    if idx >= len(fx["frames"]):
        return None
    lines = fx["frames"][idx]
    w = max(len(l) for l in lines)
    # Center the frame on (col, row) and subtract rise * age so the effect drifts upward over time.
    row = fx["row"] - len(lines) // 2 - int(age * fx["rise"])
    return (lines, fx["col"] - w // 2, row, fx["colors"][idx])


# A small hit effect (2 frames, 0.1 s each).
def spark(col, row, now, color=YELLOW):
    return make_effect(SPARK_FRAMES, [color, DIM], 0.1, col, row, now)


# A big explosion (3 frames, 0.15 s each: yellow, red, dim).
def boom(col, row, now):
    return make_effect(BOOM_FRAMES, [YELLOW + BOLD, RED + BOLD, DIM], 0.15, col, row, now)


# Two centered rows: the note NAMES and below them the KEY NUMBERS. Notes that were already typed
# correctly (first `typed` entries) are drawn green. `cell` = width per note.
def cells_rows(notes, typed=0, cell=5):
    """Two centered rows: note names and their key numbers (typed ones green)."""
    # Left indent that centers the whole row of notes.
    indent = " " * ((INNER_W - cell * len(notes)) // 2)
    names = nums = indent
    # One cell per note: green if i < typed. .center(cell) pads to the cell width.
    for i, n in enumerate(notes):
        col = GREEN if i < typed else ""
        names += f"{col}{NOTES[n - 1].center(cell)}{RESET}"
        nums += f"{col}{str(n).center(cell)}{RESET}"
    return [names, nums]


# Chooses the next monster. Only monsters whose min_stage has been reached are in the pool; the
# weight 1 + min_stage makes later monsters more likely (slime 1, dragon 13). random.choices() does
# a
# weighted random pick (it returns a list, so [0]).
def pick_monster(stage):
    pool = [k for k, m in MONSTERS.items() if m["min_stage"] <= stage]
    weights = [1 + MONSTERS[k]["min_stage"] for k in pool]   # newer monsters more likely
    return random.choices(pool, weights)[0]       # choices() = weighted random pick


# Creates the random note sequence for one hit. Length grows with the stage: 4 + stage // 4, at most
# 8.
def new_sequence(stage, mdef):
    """Sequences get longer the further you get (4 notes, up to 8)."""
    # Stage 0-3 -> 4 notes, 4-7 -> 5, ... capped at 8.
    length = min(8, 4 + stage // 4)
    while True:
        # Keep drawing random sequences until one does NOT start with the monster's killer spell, so
        # the
        # spell and the normal sequence can never be confused with each other.
        seq = [random.randint(1, 8) for _ in range(length)]
        if seq[:len(mdef["cast"])] != mdef["cast"]:   # never start like the killer spell
            return seq


# Seconds the player gets for a monster = also the time the monster needs to walk up to the cat.
def time_limit(mdef, stage, seq_len):
    # Time per sequence: 0.8 s per note + 2 s base.
    per_seq = seq_len * 0.8 + 2.0
    # Monsters get 2.5 percent faster each stage, up to half the time (reached at stage 20).
    speed = max(0.5, 1 - 0.025 * stage)               # monsters get faster
    # Total = hp * per-sequence time * speed factor, but never below 5 seconds.
    return max(5.0, mdef["hp"] * per_seq * speed)


# ONE MONSTER FIGHT. Returns "win", "lose" or "flee". Structure:
# (1) set up state, (2) define helper functions monster_col() and draw(), (3) main loop: draw,
# read a key, handle it, repeat.
# The monster walks from the right edge toward the cat; its position is simply
# elapsed_time / time_limit, so it reaches the cat exactly when the time runs out.
def fight(keys, save, key, mdef, stage, score):
    """One monster. Returns 'win', 'lose' or 'flee'."""
    # The player's equipped character (for its sprites).
    char = CHARACTERS[save["character"]]
    # Does the player own this monster's killer spell?
    owns = key in save["spells"]
    # The killer spell's note combo (list of key numbers).
    spell = mdef["cast"]
    # The note sequence currently required for a normal hit.
    seq = new_sequence(stage, mdef)
    # Remaining hit points (counts down to 0).
    hp = mdef["hp"]
    # Total time allowed.
    limit = time_limit(mdef, stage, len(seq))
    # Moment the fight started: all timing is "now - start".
    start = time.time()
    # The notes the player has typed so far in the current attempt.
    buf = []                                    # notes typed so far
    # A feedback message and the time until which it is shown.
    msg, msg_until = f"A {mdef['name']} approaches!", start + 1.5
    # Timestamps: until when the cat shows her attack pose / the monster flashes yellow after a hit.
    attack_until = hit_flash_until = 0.0
    # Flying notes and running animations. The draw() function updates and cleans these lists.
    projectiles, effects = [], []
    # When the monster dies this gets the progress value at that moment, so the monster stops
    # walking
    # while the victory animation plays. None = still walking.
    frozen = None                               # set when the monster dies: stops it walking

    # Monster width/height, and the cat's attack-sprite width.
    mw, mh = max(len(l) for l in mdef["art"]), len(mdef["art"])
    cat_w = max(len(l) for l in char["attack"])
    # The monster enters at the right edge and walks left until it touches the cat.
    start_col = INNER_W - mw - 2                # monster appears at the right...
    # Where the walk ends: just right of the cat (2 columns of space).
    end_col = CAT_X + cat_w + 2                 # ...and walks until it touches the cat
    # Roughly the vertical middle of the monster, where hits and effects appear.
    target_row = GROUND_ROW - mh // 2 - 1       # roughly the monster's middle

    # Monster's column right now = linear interpolation from start_col to end_col. Progress is
    # clamped
    # to [0, 1]; when frozen, the frozen progress is used.
    def monster_col(now):
        # Progress from 0.0 (just appeared) to 1.0 (reached the cat).
        prog = frozen if frozen is not None else (now - start) / limit
        return int(start_col + (end_col - start_col) * min(max(prog, 0.0), 1.0))

    # Paints ONE complete battle frame. It is a function INSIDE fight(), so it can read the
    # variables of
    # fight() (hp, buf, msg ...) directly. Closures look the value up when they are CALLED, so
    # draw() always
    # sees the latest values. It also acts as the "update step": it removes projectiles that hit and
    # effects that ended.
    def draw(now, monster_visible=True):
        """Paint one complete battle frame. (This is a function *inside* a
        function: it can read hp, buf, msg, ... of fight() directly.)"""
        # Seconds left (frozen = the time at which the monster died).
        remaining = limit - ((now - start) if frozen is None else frozen * limit)
        # Current monster column.
        mcol = monster_col(now)
        # Transparent things to paint on top of the scene this frame.
        overlays = []
        # Each projectile is a flying note text. [:] iterates over a COPY of the list, which makes
        # it safe to
        # remove items inside the loop.
        for pr in projectiles[:]:                # [:] = loop over a copy, so we may delete
            # Current x position = start + elapsed * speed.
            x = pr["x0"] + (now - pr["t0"]) * PROJECTILE_SPEED
            # CORRECT notes fly toward the monster.
            if pr["good"]:
                # Distance from the paw to the monster now (at least 1, avoiding division by zero).
                span = max(1, mcol - pr["x0"])
                # How far along the way (0 to 1).
                frac = min(1.0, (x - pr["x0"]) / span)
                # The height changes gradually from the paw's row to the monster's row, so the note
                # flies diagonally
                # to its target.
                y = round(pr["y0"] + (target_row - pr["y0"]) * frac)  # aim at the monster
                # The front of the note reached the monster: remove it and show a hit spark there.
                if x + len(pr["text"]) >= mcol:
                    projectiles.remove(pr)
                    effects.append(spark(mcol, y, now))
                    continue
            else:
                # WRONG notes fly straight ahead...
                y = pr["y0"]
                # ...and fizzle out after 8 columns with a red spark.
                if x - pr["x0"] > 8:             # a wrong note fizzles out quickly
                    projectiles.remove(pr)
                    effects.append(spark(int(x), y, now, RED))
                    continue
            # Still flying: paint it this frame (text, column, row, color).
            overlays.append(([pr["text"]], int(x), y, pr["color"]))
        # Effects: get the frame to show now; finished ones are removed.
        for fx in effects[:]:
            ov = effect_overlay(fx, now)
            if ov is None:
                effects.remove(fx)
            else:
                overlays.append(ov)

        # Which cat pose? Attack pose right after a key press, otherwise alternating idle poses.
        pose = char["attack"] if now < attack_until else \
            char["idle"][int(now / IDLE_SWITCH_SECONDS) % len(char["idle"])]
        # Sprite list: the cat first (no color, no lift).
        sprites = [(pose, CAT_X, "", 0)]
        # The monster disappears in the explosion at the end of a victory, so it is optional.
        if monster_visible:
            # Monster color: flashes yellow right after being hit, turns red when less than 25
            # percent of the time
            # remains (it "panics"), otherwise normal.
            color = (YELLOW + BOLD) if now < hit_flash_until else \
                RED if remaining < limit * 0.25 else ""        # panics when time is low
            # The little hop: every other quarter second the monster is lifted by one row while it
            # walks.
            hop = 1 if (frozen is None and int(now * 4) % 2 == 0) else 0   # little hops
            # Add the monster to the sprite list.
            sprites.append((mdef["art"], mcol, color, hop))
        # Paint the picture.
        scene = render_scene(now, sprites, overlays)

        # Assemble the full screen top to bottom: status line, scene, hp bar, time bar, expected
        # sequence,
        # optional spell hint, all 8 keys, typed notes, message.
        out = [f" {BOLD}Stage {stage + 1}{RESET}    Score {score}    Coins {save['coins']}"]
        # The scene rows.
        out += scene
        out.append("")
        # HP bar (red) and the numbers.
        out.append(f" {mdef['name']:<10} HP   {bar(hp, mdef['hp'], 20, RED)} {hp}/{mdef['hp']}")
        # Time bar (yellow) with the seconds left.
        out.append(f" {'':<10} TIME {bar(remaining, limit, 20, YELLOW)} {max(remaining, 0):4.1f}s")
        out.append("")
        # The sequence to play. Notes already typed correctly turn green.
        out.append(center_text(f"Expected sequence for {mdef['name']}:"))
        # Note names and keys of the sequence.
        out += cells_rows(seq, typed=len(buf))
        # If the spell is owned, show its (shorter) combo in cyan; otherwise a hint to buy it, plus
        # two blank
        # rows so the layout does not jump.
        if owns:
            out.append(center_text(f"{CYAN}Spell '{mdef['spell']}' (instant kill):{RESET}"))
            out += cells_rows(spell, typed=len(buf))
        else:
            out.append(center_text(f"{DIM}(Buy '{mdef['spell']}' in the spell book to skip this!){RESET}"))
            out += ["", ""]
        out.append("")
        # A permanent reference of all 8 keys.
        out += cells_rows([1, 2, 3, 4, 5, 6, 7, 8])
        out.append("")
        # What the player has typed so far.
        out.append(" Your input: " + " ".join(NOTES[n - 1] for n in buf))
        # The feedback message (only until its expiry time).
        out.append(" " + (YELLOW + msg + RESET if msg and now < msg_until else ""))
        # Put it all into the frame and print it.
        show(framed(out, "keys 1-8: play notes | BACKSPACE: undo | Q: flee"))

    # THE MAIN FIGHT LOOP: check time -> draw -> read a key -> handle the key.
    while True:
        now = time.time()
        # Time is up: the monster reached the cat.
        if limit - (now - start) <= 0:
            return "lose"
        # Paint the current frame.
        draw(now)
        # Wait up to 50 ms for a key (about 20 frames per second while idle).
        k = keys.get(0.05)
        if k is None:
            continue
        # Q/ESC = flee the battle.
        if k in ("q", "esc"):
            return "flee"
        # Backspace: remove the last typed note (undo).
        if k in ("\x7f", "\b"):
            buf = buf[:-1]
            continue
        # Ignore every other key.
        if k not in NOTE_KEYS:
            continue

        now = time.time()
        # A note key (1-8). Update the time (the earlier `now` may be old), convert to int.
        n = int(k)
        # Play the matching sound.
        AUDIO.note(n)                                   # do re mi fa sol la si dohigh
        # Add the note to the typed notes.
        buf.append(n)
        # The cat pushes with her paw for a short time.
        attack_until = now + ATTACK_SECONDS             # <- the cat pushes with her paw!
        # Compare the typed notes with the START of the spell / the sequence: seq[:len(buf)] is the
        # first
        # len(buf) notes. A note is "good" if the typed notes are still a correct beginning of
        # either.
        on_spell = owns and spell[:len(buf)] == buf     # slice [:len] = "the first len(buf) notes"
        # Prefix check against the normal sequence.
        on_seq = seq[:len(buf)] == buf
        # Correct so far?
        good = on_spell or on_seq
        # Launch a projectile from the paw tip: the text "~~DO" etc., cyan if the note was right,
        # red if wrong.
        # (Spawn row = top of the sprite + PAW_ROW.)
        projectiles.append({"x0": CAT_X + PAW_COL, "y0": GROUND_ROW - len(char["attack"]) + PAW_ROW,
                            "t0": now, "text": "~~" + NOTES[n - 1], "good": good,
                            "color": (CYAN + BOLD) if good else RED})
        # Center of the monster (hits and explosions appear here).
        center = (monster_col(now) + mw // 2, target_row)
        # Wrong note: the player must start the sequence again.
        if not good:
            buf = []
            msg, msg_until = "Wrong note! Start again.", now + 1.0
        # Spell completed: the monster dies instantly (hp = 0).
        elif owns and buf == spell:
            hp, buf = 0, []
            msg, msg_until = f"{mdef['spell']}! Instant kill!", now + 1.0
        # Normal sequence completed: 1 damage, an explosion and a flash...
        elif buf == seq:
            hp, buf = hp - 1, []
            msg, msg_until = "Direct hit!", now + 1.0
            hit_flash_until = now + 0.25
            effects.append(boom(*center, now))
            # ...and if the monster is still alive, it gets a NEW sequence.
            if hp > 0:
                seq = new_sequence(stage, mdef)

        # VICTORY ANIMATION.
        if hp <= 0:                                     # victory animation
            # Random death sound.
            AUDIO.enemy_dead()                          # random EnemyDead1-3
            # Freeze the monster's walk at its current progress.
            frozen = (now - start) / limit              # the monster stops walking
            effects.append(boom(*center, now))
            # A floating "+coins" text that rises upward (rise=4 rows/second).
            effects.append(make_effect([[f"+{mdef['coins']}"]], [YELLOW + BOLD], 0.9,
                                       center[0], center[1] - 2, now, rise=4))
            # Message for the player.
            msg, msg_until = f"Defeated! +{mdef['coins']} coins", now + 5
            t0 = now
            # Play the animation for 1 second. The monster is hidden after 0.15 s (it vanishes in
            # the blast).
            while time.time() - t0 < 1.0:
                t = time.time()
                # The monster is visible only during the first 0.15 s.
                draw(t, monster_visible=(t - t0) < 0.15)    # monster vanishes in the blast
                # About 30 frames per second.
                time.sleep(0.03)
            # Throw away key presses that were typed during the animation, then report the win.
            keys.drain()
            return "win"


# The monster reached the cat: explosions, shaking, then a tombstone (14 frames x 0.12 s).
def death_animation(mdef, char):
    """The monster reaches the cat: explosions, shaking, then a tombstone."""
    # The monster stands right next to the cat.
    end_col = CAT_X + max(len(l) for l in char["attack"]) + 2
    # Active explosions.
    effects = []
    # Frame counter i.
    for i in range(14):
        now = time.time()
        # Three explosions on the cat at frames 0, 3, 6.
        if i in (0, 3, 6):
            effects.append(boom(CAT_X + 12, GROUND_ROW - 4, now))
        # Current picture of each explosion (finished ones return None and are skipped).
        overlays = [o for o in (effect_overlay(f, now) for f in effects) if o]
        # First 9 frames the cat stands (attack pose); after that the tombstone replaces her.
        pose = char["attack"] if i < 9 else TOMBSTONE
        # On odd frames she shakes 2 columns to the right and flashes red.
        shake = 2 if (i % 2 and i < 9) else 0
        # Red flash on odd frames while alive.
        color = RED if (i % 2 and i < 9) else ""
        # Paint the picture with the cat/tombstone and the monster beside her.
        scene = render_scene(now, [(pose, CAT_X + shake, color, 0),
                                   (mdef["art"], end_col, "", 0)], overlays)
        # Show it (no hint text).
        show(framed([""] + scene))
        # 0.12 s per frame.
        time.sleep(0.12)


# Result screen: the scene, a headline and the statistics, then wait for a key.
def end_screen(keys, scene, headline, stats):
    # Start with the scene.
    inner = [""] + scene + [""]
    # `headline` is a list of lines for the big "YOU DIED" banner (red + bold), or a plain string
    # for a
    # normal headline (bold). isinstance() checks which one it is.
    inner += [center_text(RED + BOLD + l + RESET) for l in headline] if isinstance(headline, list) \
        else [center_text(BOLD + headline + RESET)]
    # Add the statistic lines centered.
    inner += [""] + [center_text(l) for l in stats]
    show(framed(inner, "press any key"))
    # Wait for any key.
    wait_key(keys)


# A WHOLE RUN: monster after monster, harder each time, until you die or flee. Returns nothing; all
# results are written into the save.
def battle(keys, save):
    """A whole run: monsters keep coming, harder and harder, until you die."""
    # Counters for this run: stage = monsters defeated, score points, coins earned.
    stage = score = earned = 0
    # How the run ended and which monster was the last (for the death scene).
    outcome, last = "lose", None
    # Remember the level at the start to detect a LEVEL UP afterwards.
    level_before = level_of(save)
    char = CHARACTERS[save["character"]]
    keys.drain()
    # No background music during battle.
    AUDIO.set_mode("none")                              # no background music in battle
    # One pass = one monster.
    while True:
        # Choose a monster for this stage.
        key = pick_monster(stage)
        mdef = MONSTERS[key]
        # Run the fight (blocks until it ends).
        result = fight(keys, save, key, mdef, stage, score)
        # Win: score = 10 * hp of the monster + stage number, coins and kill statistics are updated,
        # then
        # the next stage.
        if result == "win":
            # Score for the kill; tougher monsters and later stages give more.
            score += 10 * mdef["hp"] + stage
            earned += mdef["coins"]
            save["coins"] += mdef["coins"]
            save["total_kills"] += 1
            save["kills"][key] = save["kills"].get(key, 0) + 1
            # Next monster will be one stage harder.
            stage += 1
        else:
            # "lose" or "flee": remember it and end the run.
            outcome, last = result, mdef
            break

    # Update the permanent statistics and save immediately (this also happens when you flee).
    save["games_played"] += 1
    save["total_score"] += score
    save["max_score"] = max(save["max_score"], score)
    save["max_stage"] = max(save["max_stage"], stage)
    write_save(save)

    # Text lines for the end screen.
    stats = [f"Monsters defeated: {stage}", f"Score: {score}   (best: {save['max_score']})",
             f"Coins earned: {earned}   (total: {save['coins']})"]
    # Highlight a level-up.
    if level_of(save) > level_before:
        stats.append(f"{GREEN}{BOLD}LEVEL UP!  You are now level {level_of(save)}{RESET}")
    now = time.time()
    # DEATH: the game-over jingle plays, then the death animation, the tombstone scene and a "YOU
    # DIED"
    # banner. Afterwards the menu music returns.
    if outcome == "lose":
        # Stops everything and plays only the game over jingle.
        AUDIO.set_mode("gameover")                      # stops everything, plays only Game Over
        death_animation(last, char)
        end_col = CAT_X + max(len(l) for l in char["attack"]) + 2
        scene = render_scene(now, [(TOMBSTONE, CAT_X, "", 0), (last["art"], end_col, "", 0)], [])
        end_screen(keys, scene, banner("YOU DIED"), stats)
        AUDIO.set_mode("menu")                          # back to the menus: music returns
    else:
        AUDIO.set_mode("menu")
        # FLED: set the menu music again and show the cat alive with a "You fled the battle"
        # headline.
        scene = render_scene(now, [(char["idle"][0], CAT_X, "", 0)], [])
        end_screen(keys, scene, "You fled the battle", stats)


# =============================================================================
# 13. MAIN
# =============================================================================
# The title screen loop. It runs until the player presses Q on the title screen.
def title_loop(keys):
    while True:
        # Re-read the save every loop so that "CONTINUE" appears as soon as a save exists.
        save = load_save()               # re-read each time: Continue appears once a save exists
        # Which entries exist: CONTINUE only if a save was found.
        available = (["continue"] if save else []) + ["new", "options", "tutorial"]
        # Keep the order defined in TITLE_ITEMS (top to bottom on screen). The selection index
        # refers to this list.
        order = [k for k in TITLE_ITEMS if k in available]    # keeps the menu's order
        # The lambda is the draw function: it renders the title screen with the highlighted entry.
        sel = choose(keys, len(order),
                     lambda s: show(render_title(available, order[s])))
        # Q on the title screen = quit the game.
        if sel is None:
            return                       # Q on the title screen = quit the game
        # Which entry was chosen.
        choice = order[sel]
        # CONTINUE: open the hub with the loaded save.
        if choice == "continue":
            hub(keys, save)
        # NEW GAME: if a save exists, first ask for confirmation, because creating a new game
        # overwrites it.
        elif choice == "new":
            # Answer was not "yes": go back to the title.
            if save and not confirm(keys, "Starting over erases your saved game. Continue?"):
                continue
            # Pick a character + name; the function returns None if cancelled.
            new = create_character(keys)
            if new:
                hub(keys, new)
        # OPTIONS or TUTORIAL.
        elif choice == "options":
            options_screen(keys)
        else:
            tutorial_screen(keys)


# Program entry point: prepares the terminal, runs the intro and the title loop, and ALWAYS restores
# the terminal afterwards.
def main():
    # The game needs a real terminal (keyboard reading and ANSI codes). If input comes from a pipe
    # or
    # an IDE console that is not a TTY, print a message and stop.
    if not sys.stdin.isatty():
        print("Please run this game in a normal terminal window.")
        return
    # A trick: running an empty command makes older Windows consoles switch on ANSI escape code
    # support.
    # It is harmless on other systems.
    os.system("")                        # turns on ANSI codes in older Windows consoles
    # Start the sound system (silent if pygame or the sound files are missing).
    AUDIO.init()                         # starts the sound system (silent if pygame is missing)
    # "\033[?1049h" = switch to the terminal's *alternate screen* (like vim does),
    # so your normal terminal content comes back untouched when the game ends.
    # "\033[?25l" = hide the blinking cursor.
    # "\033[?1049h" switches to the terminal's ALTERNATE SCREEN (like vim), so the player's normal
    # terminal content comes back untouched when the game ends. "\033[?25l" hides the blinking
    # cursor.
    sys.stdout.write("\033[?1049h\033[?25l")
    # try/except/finally: whatever happens (normal exit, Ctrl+C, error), the finally part runs so
    # the
    # terminal is restored.
    try:
        # Switch the keyboard to instant mode; Keys.__exit__ restores it automatically at the end.
        with Keys() as keys:
            # Window too small and the player pressed Q: return. (The `return` inside try still runs
            # the finally
            # block first; the "Thanks for playing!" line is skipped in that case.)
            if not ensure_size(keys):             # window too small and the player quit
                return
            # Intro animation, then the title loop. The game stays inside title_loop until the
            # player quits.
            falling_intro(keys)
            title_loop(keys)
    # Ctrl+C: leave quietly, no error text.
    except KeyboardInterrupt:                     # Ctrl+C = leave quietly
        pass
    # Runs no matter how we leave.
    finally:                                      # runs no matter how we leave
        # Shut down the audio.
        AUDIO.close()
        sys.stdout.write("\033[?25h\033[?1049l")  # show cursor, leave alternate screen
        sys.stdout.flush()
    # Printed AFTER leaving the alternate screen, so it stays visible in your normal terminal.
    print("Thanks for playing!")


# Standard Python idiom: main() runs only when this file is started directly (python main47.py),
# not when it is imported from another file.
if __name__ == "__main__":
    main()