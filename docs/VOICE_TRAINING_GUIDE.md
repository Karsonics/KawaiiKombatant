╔══════════════════════════════════════════════════════════════╗
║     GPT-SoVITS VOICE TRAINING & AUDIO QUALITY GUIDE           ║
║     For Kuro the Tsundere Wolf Girl                           ║
╚══════════════════════════════════════════════════════════════╝

────────────────────────────────────────────────────────────
  WHY FINE-TUNE?
────────────────────────────────────────────────────────────

Currently Kuro uses ZERO-SHOT voice cloning — a single 9-second reference
clip (megumi_clean.wav). This gives "good" quality but:

  ✗ Voice can sound "off" on longer sentences
  ✗ Prosody sometimes robotic or flat
  ✗ Limited emotional range (only whatever the ref clip conveys)
  ✗ Inconsistent between generations

FEW-SHOT fine-tuning with 1-5 minutes of Kuro-specific audio gives:

  ✓ Near-perfect voice match — consistently sounds like Kuro
  ✓ Natural prosody and rhythm — smoother delivery
  ✓ Wider emotional range — if training data is emotionally varied
  ✓ Stable across long sentences — no degradation
  ✓ Per-mood models — happy Kuro, annoyed Kuro, etc. sound distinct

────────────────────────────────────────────────────────────
  TRAINING DATA: WHAT YOU NEED
────────────────────────────────────────────────────────────

  MINIMUM (1 minute) — for better voice quality:
    └── 1 minute of clean Kuro audio, any emotion

  RECOMMENDED (3-5 minutes) — for studio quality:
    ├── neutral.wav    : 45-60s of normal talking
    ├── happy.wav      : 30-45s of happy/excited delivery
    ├── annoyed.wav    : 30-45s of tsundere/annoyed delivery
    ├── excited.wav    : 30-45s of energetic delivery
    └── curious.wav    : 30-45s of curious/interested delivery

  PER-MOOD MODELS (best) — train 5 separate models:
    Each mood gets its own 30-60s training clip → dedicated model
    TTS manager auto-selects based on detected mood

────────────────────────────────────────────────────────────
  AUDIO QUALITY REQUIREMENTS
────────────────────────────────────────────────────────────

  FORMAT:
    ✓ WAV, mono, 44100 Hz or 48000 Hz, 16-bit PCM
    ✓ Clean — no background music, noise, echo, reverb
    ✓ Single speaker — Kuro's voice only
    ✓ 2-13 seconds per segment (auto-sliced)
    ✓ Clear pronunciation — no mumbling

  BAD AUDIO = BAD MODEL. If the training audio has:
    ✗ Background music → model learns music as "voice"
    ✗ Noise/hiss → model reproduces the noise
    ✗ Multiple speakers → model blends voices
    ✗ Compression artifacts → metallic output

────────────────────────────────────────────────────────────
  HOW TO GET KURO TRAINING DATA
────────────────────────────────────────────────────────────

  OPTION 1: Record Yourself (BEST QUALITY)
  ─────────────────────────────────────────
  1. Use Audacity or any audio recorder
  2. Record in a quiet room, close to the mic
  3. Speak naturally as Kuro:
     - Neutral:  "Hello! Welcome to the stream. How are you today?"
     - Happy:    "Hehe, my tail is wagging! I mean... it's just a reflex!"
     - Annoyed:  "Hmph! Baka! What do you want?! Don't bother me!"
     - Excited:  "Oh! Did you bring snacks?! Really?! Let me see!"
     - Curious:  "Hmm? What's that? Are you hiding something from me?"
  4. Export as mono WAV, 44100 Hz

  OPTION 2: Extract from Anime/Game Voice Lines
  ───────────────────────────────────────────────
  1. Visit sounds-resource.com or similar
  2. Find a tsundere character voice pack (e.g. Taiga, Louise, etc.)
  3. Extract clean voice lines without background music
  4. Use UVR5 (built into GPT-SoVITS WebUI) to remove any remaining music
  5. Trim to 2-13 second segments

  OPTION 3: Generate with Fish Speech S2-Pro
  ───────────────────────────────────────────
  1. Start Fish Speech when GPU available (needs 24GB+)
  2. Use emotion tags to generate mood variants:
     [happy] Hehe, my tail is wagging!
     [angry] Hmph! Baka! What do you want?!
     [excited] Oh! Did you bring snacks?!
     [curious] Hmm? What's that?
  3. Save each as separate WAV files
  4. These can serve as training data for GPT-SoVITS

  OPTION 4: Use Qwen3-TTS Voice Clone
  ────────────────────────────────────
  1. Qwen3-TTS supports instruction-based emotion:
     "Speak in a happy, slightly embarrassed tone"
     "Speak in an angry tsundere tone, add baka energy"
  2. Generate multiple mood variants from megumi_clean.wav
  3. Use the output as training data for GPT-SoVITS

────────────────────────────────────────────────────────────
  TRAINING PIPELINE (WebUI)
────────────────────────────────────────────────────────────

  Step 0: Start the WebUI
  ────────────────────────
  conda activate GPTSoVits
  cd /mnt/storage/GPT-SoVITS
  PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/mnt/storage/GPT-SoVITS python webui.py
  # Opens at http://localhost:9872

  Step 1: Audio Pre-processing (Tab "0-Fetch Dataset")
  ─────────────────────────────────────────────────────
  a) "0a-Vocal Separation" (if audio has background)
     → Removes music/noise, keeps only voice
     → Use UVR5 MDX-Net model for best results

  b) "0b-Audio Splitting"
     → Cuts long audio into 2-13 second segments
     → Input: your training WAV
     → Output: sliced segments in output/slicer_opt/

  c) "0c-Speech Recognition"
     → Auto-transcribes each segment
     → Use "faster-whisper" with "large-v3" model
     → Output: .list file with audio_path|speaker|language|text

  d) "0d-Proofread Annotations"
     → Manually check/correct transcription errors
     → Critical step — wrong text = wrong training

  Step 2: Dataset Formatting (Tab "1A-Dataset Formatting")
  ─────────────────────────────────────────────────────────
  → Click "One-Click Triple Action"
  → This handles text tokenization, feature extraction, etc.
  → Takes ~2-5 minutes

  Step 3: SoVITS Training (Tab "1B-Fine-tuning Training")
  ────────────────────────────────────────────────────────
  Settings:
    batch_size:    4 (adjust down if OOM)
    total_epoch:   8 (range 6-15)
    save_every:    2 (keep multiple checkpoints)

  → Click "Start SoVITS Training"
  → This trains the ACOUSTIC MODEL (timbre/voice quality)
  → Time: ~15-25 min on your 9060 XT (1 min data)

  Step 4: GPT Training
  ─────────────────────
  Settings:
    batch_size:    2 (GPT needs more VRAM)
    total_epoch:   15 (range 12-18)
    save_every:    3

  → Click "Start GPT Training"
  → This trains the SEMANTIC MODEL (prosody/emotion/flow)
  → Time: ~10-15 min on your 9060 XT

  Step 5: Inference with Trained Model
  ─────────────────────────────────────
  → Go to "1C-Inference" tab
  → Click "Refresh model path"
  → Select your trained GPT + SoVITS weights
  → Test with sample text

  Step 6: Export for API Use
  ───────────────────────────
  Find trained models in:
    GPT_SoVITS/SoVITS_weights_v2ProPlus/Kuro_e8_s128.pth
    GPT_SoVITS/GPT_weights_v2ProPlus/Kuro_e15_s128.ckpt

  Point tts_infer.yaml or API to these paths.

────────────────────────────────────────────────────────────
  PER-MOOD MODEL STRATEGY
────────────────────────────────────────────────────────────

  STRATEGY A: One Model, Diverse Data (Simpler)
  ──────────────────────────────────────────────
  Train ONE model on 3-5 minutes of mixed-emotion audio.
  The GPT learns to vary delivery based on context.

  Pros: One training run, simpler setup
  Cons: Less distinct mood differences

  STRATEGY B: Five Per-Mood Models (BEST)
  ────────────────────────────────────────
  Train 5 separate models, each on 30-60s of one emotion:

    Model           Training Data          Used When
    ─────────────────────────────────────────────────
    Kuro_neutral     neutral.wav (45s)      mood=neutral, curious
    Kuro_happy       happy.wav (30s)        mood=happy
    Kuro_annoyed     annoyed.wav (30s)      mood=annoyed
    Kuro_excited     excited.wav (30s)      mood=excited
    Kuro_default     all mixed (60s)        fallback

  TTS manager routes mood → model automatically:

    sovits_config.yaml:
      moods:
        neutral:  { model: "Kuro_neutral" }
        happy:    { model: "Kuro_happy" }
        annoyed:  { model: "Kuro_annoyed" }
        excited:  { model: "Kuro_excited" }

  Strategy B gives distinctly different deliveries per mood.
  Happy Kuro actually SOUNDS happy. Annoyed Kuro has real tsundere bite.

────────────────────────────────────────────────────────────
  TRAINING TIME ESTIMATES (RX 9060 XT 16GB)
────────────────────────────────────────────────────────────

  Data       SoVITS     GPT        Total
  ──────────────────────────────────────────
  1 minute   15-20 min  10-15 min  25-35 min
  3 minutes  20-30 min  15-20 min  35-50 min
  5 minutes  25-35 min  20-25 min  45-60 min

  Per-mood (30s each × 5):
    SoVITS: ~10 min per model × 5 = 50 min
    GPT:    ~5 min per model × 5 = 25 min
    Total:  ~75 min for all 5 moods

────────────────────────────────────────────────────────────
  TRAINING TIPS
────────────────────────────────────────────────────────────

  ✓ Start with 1 minute of neutral audio first — test quality
  ✓ Use batch_size=1 if you get OOM errors
  ✓ Check "save every 2 epochs" to keep intermediate checkpoints
  ✓ Monitor loss — it should decrease and plateau
  ✓ Test each checkpoint — sometimes middle epochs sound best
  ✓ For mood models, keep training data PURE (one emotion per model)
  ✓ Proofread ASR output carefully — wrong text ruins training
  ✓ Reference audio quality matters more than quantity
  ✓ Use the same mic/setup for all recordings for consistency

────────────────────────────────────────────────────────────
  AFTER TRAINING: API INTEGRATION
────────────────────────────────────────────────────────────

  1. Place trained models in GPT_SoVITS/pretrained_models/custom/

  2. Add to sovits_config.yaml:

     per_mood_models:
       neutral:
         gpt: "GPT_weights_v2ProPlus/Kuro_neutral_e15_s128.ckpt"
         sovits: "SoVITS_weights_v2ProPlus/Kuro_neutral_e8_s128.pth"
       happy:
         gpt: "GPT_weights_v2ProPlus/Kuro_happy_e15_s128.ckpt"
         sovits: "SoVITS_weights_v2ProPlus/Kuro_happy_e8_s128.pth"
       ...etc

  3. Update GPTSovitsBackend in gpt_sovits.py to:
     - Accept model_path override
     - Load per-mood weights based on detected mood
     - Fall back to default model if per-mood not available

  4. Restart the API server with new config.

────────────────────────────────────────────────────────────
  THE IDEAL END STATE
────────────────────────────────────────────────────────────

  Kuro says "baka" when annoyed:
    → TTS manager detects mood=annoyed
    → Selects Kuro_annoyed fine-tuned model
    → Delivers with genuine tsundere bite
    → Sounds like the SAME Kuro, just ANNOYED

  Kuro says "hehe" when happy:
    → TTS manager detects mood=happy
    → Selects Kuro_happy fine-tuned model
    → Delivers with warmth and energy
    → Tail-wagging audible in the voice

  Kuro says "hmm?" when curious:
    → Defaults to neutral model
    → Clean, consistent Kuro voice
    → Natural inquisitive tone from the GPT's learned prosody

────────────────────────────────────────────────────────────
  QUICK START: GET BETTER QUALITY IN 1 HOUR
────────────────────────────────────────────────────────────

  Priority 1 — Better voice quality (1 hour):
    1. Record 1 minute of Kuro reading natural dialogue
    2. Run through WebUI training pipeline
    3. Switch API to use trained model
    → Result: Voice sounds consistently like Kuro on all sentences

  Priority 2 — Emotional range (2-3 hours):
    1. Record 30s each of happy, annoyed, excited Kuro
    2. Train per-mood models
    3. Wire into mood routing
    → Result: Kuro's voice matches her detected mood

  Priority 3 — Studio quality (3-5 hours):
    1. Record 3-5 minutes of varied Kuro dialogue
    2. Train with more epochs, lower learning rate
    3. Compare checkpoints, pick best
    → Result: Production-grade Kuro voice