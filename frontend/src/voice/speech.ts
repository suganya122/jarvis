/**
 * Spoken replies through the computer's own voices only. Browsers can also offer
 * network voices (they send text to a cloud service); those are never used.
 */

export function localVoices(): SpeechSynthesisVoice[] {
  if (typeof speechSynthesis === "undefined") return [];
  return speechSynthesis.getVoices().filter((v) => v.localService);
}

export function canSpeak(): boolean {
  return localVoices().length > 0;
}

export function speak(text: string, lang = navigator.language || "en-US"): boolean {
  const voices = localVoices();
  if (!voices.length || !text.trim()) return false;
  const voice = voices.find((v) => v.lang === lang) ?? voices.find((v) => v.lang.startsWith(lang.slice(0, 2))) ?? voices[0];
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 2_000));
  utterance.voice = voice;
  utterance.lang = voice.lang;
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
  return true;
}

export function stopSpeaking(): void {
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
}
