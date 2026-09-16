# Repository agent instructions

- Run Python, tests, and project commands in the `robotcast-whisper-gpu` Conda environment.
- Use physical GPU 1 for transcription: set `CUDA_VISIBLE_DEVICES=1`.
- Whisper production settings are `large-v3`, CUDA, and FP16.
- Keep the SQLite database, `.robotcast/`, generated exports, and `site-dist/` out of source control.
- Validate changes with `conda run -n robotcast-whisper-gpu pytest`.
- Production publication is a separate explicit action; do not publish merely because the site was built.
