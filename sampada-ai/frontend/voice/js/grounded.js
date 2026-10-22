// Backend-grounded answers.
//
// This path runs when no LLM key is present. Every utterance that could be about
// prices, weather, soil, recommendations or PoP is resolved by calling the
// existing SAMPADA API, which already enforces the project's honesty rules:
// market returns 503 with data_unavailable, PoP is verified or honestly
// unavailable, and recommendations carry a confidence band and citations.
//
//   - Invent a soil type. The soil endpoint returns null with no evidence, and
//     that refusal is the answer.
//
// Two things the spoken output deliberately does NOT do:
//
//   - Read a citation aloud. `window_basis` is an English bibliographic string
//     ("ICAR-IISR 2023 Table 2, Central zone row: '20th June-5th July'").
//     Speaking it aloud is useless to a farmer. The window itself is spoken; the
//     source is kept for the "who says?" follow-up.
//   - Flatten a confidence band into a number. "LOW" is said as low, because a
//     bare 27% reads as a measurement.

import { splitLong } from './sentences.js';

// Enum codes are uppercase literals, not the labels shown in the UI. These came
// from GET /enums; a wrong case here is a 422 with no useful message.
const FARM_TEMPLATE = {
  label: 'Voice farm',
  land_area: 5,
  land_unit: 'ACRE',
  irrigation_type: 'RAINFED',
  water_source: 'WELL',
  district: 'Pune',
  taluka: 'Baramati',
  village: 'Voice',
  latitude: 18.15,
  longitude: 74.58,
};

// Marathi numerals for the bands, so the number is never the only signal.
const CONFIDENCE_MR = {
  HIGH: 'उच्च',
  MEDIUM: 'मध्यम',
  LOW: 'कमी',
};

export class GroundedBackend {
  constructor(apiBase = 'http://127.0.0.1:8000/api/v1') {
    this.apiBase = apiBase;
    this.token = null;
    this.farmId = null;
    this.cropCache = null;

    // Provenance for the on-screen log: what answered the last question and, when
    // a source was empty, the operator-facing reason. Never spoken, but never
    // hidden either - "no prices today" is only trustworthy if the reason is
    // visible somewhere.
    this.lastSource = null;
    this.lastUnavailable = null;
  }

  async api(method, path, body) {
    const res = await fetch(`${this.apiBase}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
      },
      // A GET must not carry a body at all, not even an empty one.
      ...(body ? { body: JSON.stringify(body) } : {}),
    });

    let payload = null;
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }

    return { status: res.status, ok: res.ok, payload };
  }

  // One throwaway demo farmer per page load, so the voice loop can answer
  // without a login screen. The backend is the auth boundary; this is only
  // avoiding friction during evaluation.
  async ensureSession() {
    if (this.token) return;
    const reg = await this.api('POST', '/auth/register', {
      name: 'Voice Farmer',
      mobile: `99${Math.floor(10000000 + Math.random() * 89999999)}`,
      pin: '1234',
      language: 'mr',
    });
    if (reg.ok) this.token = reg.payload.access_token;
  }

  async ensureFarm() {
    if (this.farmId) return;
    await this.ensureSession();
    const res = await this.api('POST', '/farms', FARM_TEMPLATE);
    if (res.ok) this.farmId = res.payload.id;
  }

  async loadCrops() {
    if (this.cropCache) return this.cropCache;
    const res = await this.api('GET', '/crops');
    this.cropCache = res.ok
      ? (res.payload.crops || []).map((c) => ({
          id: c.id,
          label: c.name?.label || c.id,
          pop: c.pop,
          popStatus: c.pop_status,
          popMessage: c.pop_message,
          popSources: c.pop_sources,
        }))
      : [];
    return this.cropCache;
  }

  // A Marathi question names the crop in Marathi, so matching against the
  // localized label is what works.
  //
  // Inflected forms are the hard part: "कांदा" is a prefix of the oblique
  // "कांद्याची" but differs by two characters, so a plain includes() misses it.
  // Matching a label against the START of the question's text with a loose
  // stem comparison is what catches कांदा/कांद्याची and सोयाबीन/सोयाबीनचा.
  //
  // Returns null when no crop is named. It deliberately does NOT fall back to
  // the first crop: answering a question about onion with soybean figures is
  // worse than asking which crop, and the backend's own rules are about exactly
  // this.
  //
  // Longest stem wins, so a query naming both कापूस and कांदा does not resolve by
  // table order.
  findCrop(text, crops) {
    let best = null;
    let bestLength = 0;

    for (const crop of crops) {
      if (!mentionsCrop(text, crop.label)) continue;
      const length = stem(crop.label).length;
      if (length > bestLength) {
        best = crop;
        bestLength = length;
      }
    }

    return best;
  }

  async* answer(userText) {
    const text = (userText || '').trim();
    const lower = text.toLowerCase();
    const crops = await this.loadCrops();

    this.lastSource = null;
    this.lastUnavailable = null;

    if (this.wantsPrice(lower)) {
      yield* this.priceAnswer(lower, crops);
      return;
    }

    if (this.wantsWeather(lower)) {
      yield* this.weatherAnswer();
      return;
    }

    if (this.wantsRecommendation(lower)) {
      yield* this.recommendationAnswer();
      return;
    }

    if (this.isSoilOnly(lower, crops)) {
      yield* this.soilAnswer();
      return;
    }

    // PoP last: either the question used PoP words outright, or it named a crop
    // and nothing more specific applied.
    if (this.wantsPopWords(lower) || this.wantsPop(lower, crops)) {
      yield* this.popAnswer(lower, crops);
      return;
    }

    for (const s of splitLong(
      'मला हा प्रश्न समजला नाही. भाव, हवामान, पेरणी, बियाणे किंवा जमीन याबद्दल विचारा.',
    )) {
      yield s;
    }
  }

  // --- intent matching -------------------------------------------------------
  // The reference implementation chained `||` with `&&` and no parentheses, which
  // silently made the last clause optional. Each intent is explicit and ordered.
  //
  // The order in answer() matters and is not cosmetic. A question naming a crop
  // plus a price word ("सोयाबीनचा भाव काय आहे") matches BOTH the PoP intent and
  // the price intent, because a named crop used to imply "tell me its package of
  // practices". The price word is the more specific signal, so specific intents
  // are tested before the crop-implies-PoP fallback.

  // Only unambiguous PoP vocabulary. The crop-implies-PoP case is handled
  // separately, after the other intents have had their chance.
  wantsPopWords(lower) {
    if (/(बियाणे|खते|पेरणीची वेळ|पेरणीच्या वेळी|पेरणी पद्धत|पेरणीची पद्धत|पद्धत सांग)/.test(lower)) {
      return true;
    }
    if (lower.includes('पिक') && /(कधी|वेळ)/.test(lower)) return true;
    return false;
  }

  // Naming a crop with no other intent is a PoP question. Deliberately not gated
  // on `pop` being present: asking about an unsourced crop is exactly when the
  // "not available yet" answer needs to be spoken.
  wantsPop(lower, crops) {
    return crops.some((c) => mentionsCrop(lower, c.label));
  }

  wantsPrice(lower) {
    return /(भाव|किंमत|बाजारभाव|मंडी)/.test(lower);
  }

  wantsWeather(lower) {
    return /(हवामान|पाऊस|पावसाची|थंडी|गरमी|अवकाश)/.test(lower);
  }

  wantsRecommendation(lower) {
    return /(शिफारस|काय पेरणी|काय लावू|कोणतं पीक|कोणता पीक|पीक निवड|सल्ला)/.test(lower);
  }

  wantsSoil(lower) {
    return /(जमीन|माती|मिट्टी|सुपीक)/.test(lower);
  }

  // "माझी जमीन कोणती" is a soil question, but "सोयाबीन कोणत्या जमिनीत" is a PoP
  // question. Soil is only claimed when the question does not also name a crop.
  isSoilOnly(lower, crops) {
    return this.wantsSoil(lower) && !crops.some((c) => mentionsCrop(lower, c.label));
  }

  // --- answers ---------------------------------------------------------------

  async* popAnswer(lower, crops) {
    const crop = this.findCrop(lower, crops);
    if (!crop) {
      // The question was about a crop, or about crops generally, but no crop in
      // the table was recognised. Asking is the honest move; answering with the
      // first crop in the table would be a confident answer to a question
      // nobody asked.
      yield* splitLong(crops.length ? 'कोणत्या पिकाबद्दल विचारत आहात? पिकाचे नाव सांगा.' : 'पिकांची यादी उपलब्ध नाही.');
      return;
    }

    // The coverage status is a field, not an inference from a null pop block.
    // Both are checked: a crop could be marked unavailable while still carrying
    // a block, and a missing status must never be read as "verified".
    const unavailable = crop.popStatus === 'unavailable' || !crop.pop;
    if (unavailable) {
      const message =
        crop.popMessage || `${crop.label} ची पेरणी पद्धत माहिती अद्याप उपलब्ध नाही.`;
      this.lastSource = null;
      yield* splitLong(message);
      return;
    }

    const pop = crop.pop;
    const window = pop.sowing?.window;
    const seed = pop.seed_rate_kg_ha;
    const fert = pop.fertilizer || {};

    let line = `${crop.label} — पेरणीची वेळ `;
    line += window
      ? `${formatDate(window[0])} ते ${formatDate(window[1])}.`
      : 'माहिती नाही.';
    if (Array.isArray(seed)) {
      line += ` बियाण्याचे प्रमाण ${seed[0]} ते ${seed[1]} किलो प्रति हेक्टर.`;
    } else if (seed) {
      line += ` बियाण्याचे प्रमाण ${seed} किलो प्रति हेक्टर.`;
    }
    if (fert.N != null || fert.P != null) {
      line += ` खत — नत्र ${fert.N ?? 0}, स्फुरद  ${fert.P ?? 0}, पालाश  ${fert.K ?? 0}, गंधक  ${fert.S ?? 0} किलो प्रति हेक्टर.`;
    }

    this.lastSource = { crop, pop };
    yield* splitLong(line);
  }

  async* priceAnswer(lower, crops) {
    const crop = this.findCrop(lower, crops);
    if (!crop) {
      yield* splitLong(crops.length ? 'कोणत्या पिकाचा भाव हवा आहे? पिकाचे नाव सांगा.' : 'पिकांची यादी उपलब्ध नाही.');
      return;
    }

    await this.ensureFarm();
    if (!this.farmId) {
      yield 'भावाची माहिती काढण्यासाठी शेत नोंदणी आवश्यक आहे.';
      return;
    }

    // `days` has a floor of 7 on the endpoint; asking for one day is a 422.
    const params = new URLSearchParams({ crop_id: crop.id, days: '7' });
    if (this.farmId) params.set('farm_id', String(this.farmId));
    const res = await this.api('GET', `/market/prices?${params}`);

    if (res.ok) {
      const records = res.payload.records || [];
      if (!records.length) {
        yield 'या पिकाचा आजचा भाव मिळाला नाही.';
        return;
      }
      const latest = records[records.length - 1];
      const modal = latest.modal_price ?? latest.min_price ?? latest.max_price;
      if (modal == null) {
        yield 'या पिकाचा आजचा भाव मिळाला नाही.';
        return;
      }
      yield `${crop.label} चा भाव — भावप्रकार ${Math.round(modal)} रुपये प्रति क्विंटल. ${latest.market ? `${latest.market} बाजारात.` : ''} ही सध्याची माहिती आहे, भविष्याची नाही.`;
      return;
    }

    // 503 is the honest-empty case. Its message is written for an operator
    // ("environment variable SAMPADA_DATA_GOV_IN_KEY is not set") and reading
    // that aloud to a farmer is noise, so the reason is kept for the on-screen
    // log and the spoken line is the farmer-facing one.
    if (res.status === 503) {
      this.lastUnavailable = res.payload?.error?.message || 'no market rows today';
      yield 'आज शेतमालाचे भाव मिळाले नाहीत. ही माहिती उपलब्ध नसल्याने सांगता येत नाही.';
      return;
    }

    this.lastUnavailable = res.payload?.error?.message || `HTTP ${res.status}`;
    yield 'भावाची माहिती काढता आली नाही.';
  }

  async* weatherAnswer() {
    await this.ensureFarm();
    if (!this.farmId) {
      yield 'हवामान काढण्यासाठी शेत नोंदणी आवश्यक आहे.';
      return;
    }

    const res = await this.api('GET', `/weather?farm_id=${this.farmId}`);
    if (!res.ok) {
      yield 'हवामानाची माहिती सध्या उपलब्ध नाही.';
      return;
    }

    const days = res.payload.days || [];
    if (!days.length) {
      yield 'हवामानाची माहिती सध्या उपलब्ध नाही.';
      return;
    }

    const today = days[0];
    const totalRain = days.slice(0, 3).reduce((sum, d) => sum + (d.rainfall_mm || 0), 0);
    const max = Math.max(...days.slice(0, 3).map((d) => d.temp_max_c ?? 0));

    yield `आज पाऊस अंदाजे ${round1(today.rainfall_mm)} मिलीमीटर. तापमान ${Math.round(today.temp_min_c ?? 0)} ते ${Math.round(today.temp_max_c ?? 0)} डिग्री.`;
    yield `पुढील तीन दिवसांत एकूण पाऊस अंदाजे ${round1(totalRain)} मिलीमीटर, आणि कमाल तापमान ${Math.round(max)} डिग्री. ही अंदाजित माहिती आहे.`;
  }

  async* recommendationAnswer() {
    await this.ensureFarm();
    if (!this.farmId) {
      yield 'शिफारस काढण्यासाठी शेत नोंदणी आवश्यक आहे.';
      return;
    }

    const res = await this.api('POST', '/recommendations', { farm_id: this.farmId });
    if (!res.ok || !res.payload?.options?.length) {
      yield 'शिफारस काढता आली नाही. उपलब्ध माहिती पुरेशी नाही.';
      return;
    }

    const [first, second] = res.payload.options;
    const band = CONFIDENCE_MR[first.confidence_band] || 'कमी';

    yield `${first.crop.label} हा सध्याचा सर्वात वरचा पर्याय आहे.`;

    // `unavailable_inputs` names what was missing; `ranking_notes` is the
    // backend's own warning that the ranking itself is weak. Both are said out
    // loud rather than dropped, because they are why confidence is low.
    const gaps = (res.payload.unavailable_inputs || []).map((m) => m.component).filter(Boolean);
    if (gaps.length) {
      yield `विश्वास पातळी ${band} आहे, कारण ${gaps.length} माहिती उपलब्ध नाही.`;
    } else {
      yield `विश्वास पातळी ${band} आहे.`;
    }

    if (res.payload.is_provisional) {
      yield 'ही शिफारस तात्पुरती आहे.';
    }

    if (second) {
      yield `दुसरा पर्याय आहे ${second.crop.label}.`;
    }

    // The backend ships localized disclaimers; one of them is used verbatim
    // rather than a hand-written near-equivalent.
    const disclaimer = (res.payload.disclaimers || [])[0];
    yield disclaimer || 'ही केवळ शिफारस आहे. हमी नाही.';
  }

  async* soilAnswer() {
    await this.ensureFarm();
    if (!this.farmId) {
      yield 'जमिनीची माहिती काढण्यासाठी शेत नोंदणी आवश्यक आहे.';
      return;
    }

    // The route is farm-scoped and takes an evidence body, not a farm_id field.
    // Empty evidence is a valid call: the endpoint returns top_soil_type = null
    // rather than guessing, which is the correct behaviour and the right thing
    // to say aloud.
    const res = await this.api('POST', `/farms/${this.farmId}/soil/estimate`, {});
    if (!res.ok || !res.payload?.top_soil_type) {
      yield 'जमिनीचा प्रकार ठरवता आला नाही. पावसानंतर जमिनीचे वर्तन सांगा, किंवा माती परीक्षण करून घ्या.';
      return;
    }

    const band = CONFIDENCE_MR[res.payload.confidence_band] || 'कमी';
    yield `तुमची जमीन ${res.payload.top_soil_type.label} प्रकारची दिसते. विश्वास पातळी ${band}.`;
    if (res.payload.needs_lab_test) {
      yield 'जास्त अचूक माहितीसाठी माती परीक्षण करून घ्या.';
    }
  }
}

// Marathi inflects the crop name, so an exact substring test misses ordinary
// questions. The stem is compared instead: a label matches when the question
// starts with it, or with it plus a vowel sign (the "आ" that turns कांदा into
// कांद्याची / कांद्याचे). Longest label first, so बाजरी is preferred over बाज when
// both could match.
export function mentionsCrop(text, label) {
  if (!label) return false;
  if (text.includes(label)) return true;

  const labelStem = stem(label);
  return labelStem.length >= 2 && stem(text).startsWith(labelStem);
}

// Devanagari combining marks that carry inflection rather than identity:
//   U+0900-U+0903  candrabindu, anusvara, visarga
//   U+093A-U+094F  all matras, virama, and the vowel-modifier signs
//   U+0951-U+0957  stress accents
//   U+0962-U+0963  vocalic l matras
// Consonants (U+0915-U+0939) and independent vowels (U+0904-U+0914) are kept.
const MATRAS = /[ऀ-ःऺ-ॏ॑-ॗॢॣ]/g;
const NON_DEVANAGARI = /[^ऀ-ॿ]/g;

// Drops those marks, leaving the consonant skeleton. This is what makes
// inflected forms match: कांदा / कांद्याची / कांद्याला all reduce to कद, and
// सोयाबीन / सोयाबीनचा to सयबन. The skeleton identifies the crop; the matras are
// what the grammar changes.
function stem(text) {
  return text
    .replace(MATRAS, '')
    .replace(NON_DEVANAGARI, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// "06-20" reads better aloud as "२० जून"; Western digits are kept because the
// frontend guide states responses use them, so the spoken form matches the
// on-screen form.
function formatDate(mmdd) {
  if (typeof mmdd !== 'string' || mmdd.length < 5) return mmdd || 'माहिती नाही';
  const [mm, dd] = mmdd.split('-');
  const months = [
    'जानेवारी', 'फेब्रुवारी', 'मार्च', 'एप्रिल', 'मे', 'जून',
    'जुलै', 'ऑगस्ट', 'सप्टेंबर', 'ऑक्टोबर', 'नोव्हेंबर', 'डिसेंबर',
  ];
  return `${Number(dd)} ${months[Number(mm) - 1] || mm}`;
}

function round1(value) {
  if (value == null) return 0;
  return Math.round(value * 10) / 10;
}
