# Recording the presentation (Team 22)

Requirements: one **MP4**, at most **6 minutes**, showing the slides **and** the live pipeline, with audio narration.
Planned length: about **5 min**. The demo is the last part of the video. Name the file `22_StreamHybridSketch_Presentation.mp4`.

## Before you press record

1. Start Docker Desktop and run `.\setup.ps1` once (2-15 min; it checks and starts everything). Leave it running.
2. Open `presentation\22_StreamHybridSketch_Presentation.pptx`. Speaker notes under each slide are the
   script, with time marks; use Presenter View (Slide Show > Use Presenter View) to read them.
3. Open a PowerShell window in the project folder, ready to type `.\demo.ps1`.
4. Do one dry run of `.\demo.ps1` so Grafana is already logged in and loads fast. It takes about 2 minutes.

## Run of show

| Time | What is on screen | Notes |
| --- | --- | --- |
| 0:00 - 2:35 | Slides 1-9 | Read the speaker notes (time marks in each); about 15-20 s per slide |
| 2:35 - 2:45 | Slide 10 (Live demo: what to watch) | Say what to watch for |
| 2:45 - 5:00 | PowerShell, then Grafana | Run `.\demo.ps1`; Grafana opens. Narrate: rounds arriving, 3 variants, drift at rounds 70 and 140 (about 1:00 and 1:55 into the run): the green baseline line drops, the other two recover within a round. As it finishes, read the closing lines from slide 10's notes and stop recording |

If you overrun, cut words from slides 3-7 first; keep the demo intact (it is the "complete project workflow").

## How to record (Windows 11, no extra software)

1. Press **Win + Shift + R** (Snipping Tool screen recording), select the whole screen.
2. Turn the **microphone** on in the recording bar (and system audio off), then click Start.
3. Present: start the slideshow, Alt+Tab to PowerShell at slide 10, run `.\demo.ps1`, show Grafana until the run ends, say the closing lines.
4. Stop; Snipping Tool saves an **MP4** to `Videos\Screen Recordings`. Check the length is under 6:00 and rename it.

Alternatives: OBS Studio (more control, also MP4), or PowerPoint's Record > Export to Video for the slide part
only (it cannot capture the demo window, so you would need to join two clips).

## Submission files

| File | What |
| --- | --- |
| `report/22_StreamHybridSketch_Report.pdf` | IEEE report (PDF) |
| `report/main.tex` + `report/tables/` + `results/figures/` | editable report source |
| `presentation/22_StreamHybridSketch_Presentation.pptx` | slides with speaker notes |
| `22_StreamHybridSketch_Presentation.mp4` | the recording (you make this) |
| repository / source zip with `README.md` | code, pipeline, setup and run instructions, tool versions |
