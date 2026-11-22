#!/usr/bin/env bash
# 素材清单批量生成（pika t2v × 9）: clip1-5 主片 + clip1 抽卡 a-d
# 用法: bash scripts/gen_clips_batch.sh   （VM 需在线且档案=h3turbo）
set -uo pipefail
H3="/Users/lucong/Library/Mobile Documents/com~apple~CloudDocs/H3"
CTL=/tmp/h3ctl
cd "$H3"

P1='integrated_multimodal_description: [Shot 1] Live-action, a low-angle medium shot follows a fluffy orange tabby cat bounding through fresh knee-deep snow in a quiet pine forest at golden hour. The camera tracks left with medium amplitude at moderate speed, staying level with the cat as it leaps, lands, and sends powdery snow spraying into the warm backlight. At 00:03.500 the cat stops, shakes snow off its head, and looks straight into the lens.

overall_soundscape: Soft crunching of snow under quick paws, light puffs of snow spraying, a brief rustle as the cat shakes its fur, and faint wind through pine branches.

non_diegetic_music: N/A'
P2='integrated_multimodal_description: [Shot 1] Live-action, a medium shot shows a young woman in a pale blue Song-dynasty hanfu standing on an arched stone bridge in a misty Jiangnan water town, holding an oil-paper umbrella. The camera arcs right with small amplitude at slow speed as she turns toward the lens, the silk sleeves and ribbons lifting in a light breeze, while a wooden boat glides beneath the bridge and petals drift across the frame.

overall_soundscape: Gentle water lapping against stone, the slow creak of a wooden oar, soft rustle of silk fabric, and distant birdsong in light drizzle.

non_diegetic_music: A guzheng plays a slow, sparse melody with soft bamboo flute sustains entering halfway through.'
P3='integrated_multimodal_description: [Shot 1] Live-action, a top-down close-up frames a bubbling Chongqing hotpot with deep red chili broth, floating dried chilies and Sichuan peppercorns. The camera pushes in with small amplitude at slow speed as a pair of chopsticks dips a strip of beef tripe into the boiling broth, swishes it three times, and lifts it out glistening with red oil and steam rising in the warm restaurant light.

overall_soundscape: Loud rolling bubbles of boiling broth, the sizzle of oil, chopsticks tapping the pot rim, and lively restaurant chatter in the background.

non_diegetic_music: N/A'
P4='integrated_multimodal_description: [Shot 1] Live-action, a wide shot shows a rain-soaked night market street in a futuristic Chinese city, neon signs reflecting in puddles, and a red neon sign reading "营业中" glowing above a noodle stall. The camera cranes down with medium amplitude at slow speed from the signs to street level as a person in a transparent raincoat walks past the stall and steam rises from the noodle pots.

overall_soundscape: Steady rain on awnings and puddles, footsteps splashing, the buzz of neon tubes, sizzling woks, and muffled crowd ambience.

non_diegetic_music: A slow synthwave bassline with soft arpeggiated synths and a gentle electronic pulse.'
P5='integrated_multimodal_description: [Shot 1] Live-action, a medium-wide shot shows a corgi wearing a tiny yellow life vest riding a surfboard on a small turquoise wave under bright sunshine. The camera tracks right with medium amplitude at moderate speed alongside the board as the corgi balances, ears flapping, spray flying around it, and at 00:03.500 the wave carries it smoothly toward the shallow beach.

overall_soundscape: Rushing waves, splashing spray, wind across the water, and a few happy barks from the corgi.

non_diegetic_music: An upbeat ukulele strum at a fast tempo with light hand claps.'

run() { # name seed prompt
  echo "=== $1 (seed $2) @ $(date '+%H:%M:%S') ==="
  $CTL gen --mode t2v --recipe pika --prompt "$3" --seed "$2" --seconds 5 --name "$1" 2>&1 | tail -2
}

run clip1 42 "$P1"
run clip2 42 "$P2"
run clip3 42 "$P3"
run clip4 42 "$P4"
run clip5 42 "$P5"
# 抽卡: clip1 同提示词 4 seed
run clip1_a 101 "$P1"
run clip1_b 102 "$P1"
run clip1_c 103 "$P1"
run clip1_d 104 "$P1"
echo "=== 全部完成 @ $(date '+%H:%M:%S') ==="
