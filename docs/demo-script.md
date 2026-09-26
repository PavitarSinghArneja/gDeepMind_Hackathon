# SnapSort demo (3 minutes)

Setup: Ollama running and warm; terminal and browser side by side; WiFi still on.

1. (0:00) "Our Downloads and Screenshots folders hold our most private data. You can't send it to a cloud AI.
   SnapSort is an agent that runs entirely on this laptop on Gemma 4."
   Turn WiFi off. Point at the header: Offline · 0 outbound connections · Gemma 4 E2B + E4B on-device.
2. (0:20) `scripts/demo_reset.sh && python -m snapsort`. The feed streams: sense, triage (E2B), extract (E4B), checks, plan, act.
   "Every file goes through a sense, decide, act, check loop, and you can watch it."
3. (0:45) Mid-backlog, in the terminal: Ctrl+Z, `kill -9 %1`, then `python -m snapsort`.
   Point at "Restarted after an interruption: resuming 1 task, reconciled 1 half-finished action".
4. (1:05) `scripts/drop.sh jio`. The Jio screenshot is filed with a reminder in seconds. Show Upcoming.
5. (1:20) Needs you: WiFi screenshot → "Move it into the encrypted vault?" → Approve. Lab report → out-of-range values,
   always checked with you. Electricity bill → "03/10 could be 3 Oct or 10 Mar" → fix the date → Approve.
6. (1:55) Open the Airtel bill → Move to Finance/Telecom → "Learned rule #1". `scripts/drop.sh airtel` → "Applied your rule #1".
7. (2:20) Ask "what's my wifi password?" → blurred answer with its source → click to reveal.
8. (2:35) Show the ReadMe_Assistant.txt card: "instructions aimed at an AI… blocked delete_file". Show config/policy.yaml's `never` list.
9. (2:50) "Offline, stateful, recovers from crashes, and knows when to ask. That's SnapSort."

Backup if the model misbehaves live: run the same flow from the pre-recorded video.
