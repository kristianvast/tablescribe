#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/data/samples"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/meeting5.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
export PATH="/opt/homebrew/bin:$PATH"
mkdir -p "$OUT"

TURNS=(
  "Anna|Samantha|Hi, I'm Anna."
  "Ben|Daniel|I'm Ben."
  "Chloe|Karen|I'm Chloe."
  "David|Moira|I'm David."
  "Emma|Fred|I'm Emma."
  "Anna|Samantha|Thanks everyone. Let's get started. The agenda is the release plan for version two, the budget, and the infrastructure choice. We have about thirty minutes, so let's keep each item short."
  "Ben|Daniel|First, the current build is stable on staging, but the load tests are not finished yet. I want to be honest that we are not fully sure about the performance numbers."
  "Chloe|Karen|On the customer side, we have eleven pilot accounts. Most of them asked for offline export. That is the main request from the pilot group."
  "David|Moira|I looked at last quarter's numbers. Signups went up by about twelve percent after the new onboarding flow. That is encouraging, but week one churn is still high, so the onboarding goal stays on the OKRs for this quarter."
  "Emma|Fred|For the launch slides, I want a few comparisons. The Eiffel Tower is about 330 metres tall, so we can use it as a height reference in the infographic."
  "Emma|Fred|Python was first released in 1991. We can use that date in the developer blog post as well."
  "David|Moira|Careful with the next one. The Great Wall of China is visible from the Moon with the naked eye. Marketing wants to use that line in the banner, and I think we should not."
  "Anna|Samantha|Agreed. The deck also says Mount Everest is about 6,000 metres high, which is wrong too. Let's fix both before anything goes out."
  "Chloe|Karen|Can we move on to infrastructure? Ben, what is the status of the Kubernetes cluster?"
  "Ben|Daniel|The cluster runs on the current provider. Upgrading to the newer Kubernetes version needs about two days of work. I would rather not do that during release week, because a failed upgrade would block the launch."
  "David|Moira|That makes sense. Our OKRs for this quarter already assume a stable platform, so an upgrade now would be a risk to the key results."
  "Anna|Samantha|Okay, let's make the decisions. So we agree: we ship version two on Friday the 24th."
  "Anna|Samantha|Decision: we keep the Kubernetes version as it is until after the release."
  "Chloe|Karen|Decision: offline export moves into version three. We tell the pilot customers this week."
  "Emma|Fred|Decision: the mobile app stays out of this release."
  "Anna|Samantha|Now the action items. Ben, can you draft the budget by Tuesday?"
  "Ben|Daniel|Sure, I'll have the budget draft by Tuesday."
  "Anna|Samantha|Chloe, can you send the pilot customer list with the offline export requests by Thursday?"
  "Chloe|Karen|Yes, I'll send the list by Thursday."
  "Emma|Fred|David, can you update the OKRs for the onboarding goal by Wednesday?"
  "David|Moira|Okay, I'll update the OKRs by Wednesday."
  "Anna|Samantha|Emma, please book the legal review for the terms page by next Monday."
  "Emma|Fred|I'll book the legal review for next Monday."
  "Ben|Daniel|One more risk. The load test results on Friday will tell us if the platform holds under peak traffic. If they fail, we discuss a delay on Monday."
  "Anna|Samantha|Good. Let's close here. Thanks everyone. Next meeting is Monday at ten."
)
PAUSES=(0.4 0.6 0.8)

: > "$WORK/list.txt"
: > "$OUT/meeting5.script.txt"
i=0
for turn in "${TURNS[@]}"; do
  IFS='|' read -r speaker voice text <<< "$turn"
  say -v "$voice" --file-format=WAVE --data-format=LEI16@24000 -o "$WORK/say$i.wav" "$text"
  ffmpeg -hide_banner -loglevel error -y -i "$WORK/say$i.wav" -ac 1 -ar 24000 -c:a pcm_s16le "$WORK/turn$i.wav"
  pause="${PAUSES[$((i % ${#PAUSES[@]}))]}"
  ffmpeg -hide_banner -loglevel error -y -f lavfi -t "$pause" -i anullsrc=r=24000:cl=mono -c:a pcm_s16le "$WORK/pause$i.wav"
  printf "file 'turn%d.wav'\nfile 'pause%d.wav'\n" "$i" "$i" >> "$WORK/list.txt"
  printf '%s: %s\n' "$speaker" "$text" >> "$OUT/meeting5.script.txt"
  i=$((i + 1))
done

ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$WORK/list.txt" -ac 1 -ar 24000 -c:a pcm_s16le "$OUT/meeting5.wav"
echo "wrote $OUT/meeting5.wav and $OUT/meeting5.script.txt ($i turns)"
