// Runs on the audio thread: converts microphone audio (usually 44.1/48 kHz
// float) to 16 kHz mono 16-bit PCM and posts ~100 ms chunks to the page.
class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;              // fractional read position into incoming audio
    this.out = new Int16Array(1600);
    this.n = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    let level = 0;
    // Simple averaging downsampler (box filter) - good enough for speech.
    while (this.pos < input.length) {
      const start = Math.floor(this.pos);
      const end = Math.min(input.length, Math.floor(this.pos + this.ratio));
      let sum = 0;
      for (let i = start; i < Math.max(end, start + 1); i++) sum += input[i];
      const s = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
      level = Math.max(level, Math.abs(s));
      this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.out.length) {
        this.port.postMessage({ pcm: this.out.buffer.slice(0), level });
        this.n = 0;
        level = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= input.length;
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
