"""Stiluri vizuale per clip. Un lot de clipuri NU trebuie să arate ca același șablon: fiecare clip primește
un stil diferit (culori de accent, intensitate zoom, mărime chunk subtitrări, poziția pop-ului)."""

STYLES = [
    dict(name="gold",    accent_ass="&H0000E6FF&", accent_hex="0xFFD400", zoom=0.05, punch=0.10, chunk=3,
         sub_margin=540, pop_x=860, pop_y=560, pop_rot=-9,
         mid_x=220, mid_y=620, mid_rot=8,
         climax_x=540, climax_y=500, climax_rot=-4),
    dict(name="cyan",    accent_ass="&H00FFE600&", accent_hex="0x00E5FF", zoom=0.07, punch=0.12, chunk=2,
         sub_margin=560, pop_x=220, pop_y=600, pop_rot=8,
         mid_x=860, mid_y=560, mid_rot=-7,
         climax_x=540, climax_y=480, climax_rot=5),
    dict(name="neon",    accent_ass="&H0014FF39&", accent_hex="0x39FF14", zoom=0.04, punch=0.09, chunk=3,
         sub_margin=520, pop_x=850, pop_y=640, pop_rot=-6,
         mid_x=240, mid_y=580, mid_rot=10,
         climax_x=540, climax_y=510, climax_rot=-3),
    dict(name="magenta", accent_ass="&H00D62BFF&", accent_hex="0xFF2BD6", zoom=0.06, punch=0.14, chunk=2,
         sub_margin=580, pop_x=230, pop_y=560, pop_rot=10,
         mid_x=850, mid_y=630, mid_rot=-9,
         climax_x=540, climax_y=490, climax_rot=4),
    dict(name="orange",  accent_ass="&H00007AFF&", accent_hex="0xFF7A00", zoom=0.05, punch=0.11, chunk=3,
         sub_margin=540, pop_x=840, pop_y=580, pop_rot=-11,
         mid_x=230, mid_y=600, mid_rot=7,
         climax_x=540, climax_y=500, climax_rot=-5),
]

# Arhetipuri narative: Gemini trebuie să alterneze între ele ca scripturile să nu aibă aceeași structură
ARCHETYPES = [
    "REACTION – the narrator reacts live, out of breath, commenting as it unfolds",
    "MINI-STORY – a tiny story with a setup, a complication and a twist ending",
    "QUESTION – opens with a provocative question, answers it in the payoff",
    "COUNTDOWN – 'three things went wrong in ten seconds' style, counting the chaos",
    "DEADPAN – dry, sarcastic understatement that contrasts with the chaos on screen",
    "WARNING – 'never do this' style mock-tutorial about what NOT to try",
]

# Categorii de pop-up-uri pentru retenție vizuală continuă
HOOK_POPS = ["WAIT!", "NO WAY?!", "WHAT?!", "LOOK!", "HOLD UP!", "BRO WAIT!", "STOP!"]
MID_POPS = ["WATCH THIS!", "HOLD ON!", "HERE IT COMES!", "SEE THIS?!", "OH NO!"]
CLIMAX_POPS = ["INSANE!", "BOOM!", "HOW?!", "UNREAL!", "PERFECT!", "WTF?!", "LEGENDARY!"]
TEASER_BADGES = ["⚡ WAIT FOR THIS...", "🔥 WATCH TILL END!", "🚨 PEAK CHAOS AHEAD", "😱 DON'T BLINK!"]

POP_TEXTS = HOOK_POPS


def style_for(clip_id: int) -> dict:
    return STYLES[(clip_id * 3 + 1) % len(STYLES)]
