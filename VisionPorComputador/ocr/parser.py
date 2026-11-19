import re
from difflib import get_close_matches
from typing import Optional, List, Dict, Any, Tuple

class MTGCardMatcher:
    TO_DIGITS = str.maketrans({
        'O': '0', 'o': '0', 'Q': '0', 'D': '0',
        'I': '1', 'i': '1', 'l': '1', '!': '1', '|': '1',
        'Z': '2', 'z': '2',
        'S': '5', 's': '5',
        'B': '8'
    })

    TO_LETTERS = str.maketrans({
        '0': 'o',
        '1': 'i', '!': 'i', '|': 'i', 'l': 'i',
        '5': 's',
        '8': 'b'
    })

    LANG_CODES = {'en', 'jp', 'fr', 'de', 'it', 'es', 'pt', 'cs', 'ct', 'ru', 'ko'}
    RARITY_CODES = {'c', 'u', 'r', 'm', 'l', 't', 's', 'p'}
    FF_GAME_TAG_PATTERN = re.compile(r'^ff[ivxlcdm]+$', re.IGNORECASE)

    def __init__(self, valid_editions):
        self.valid_editions = set(valid_editions)

    @staticmethod
    def _collapse_repeats(text: str) -> str:
        return re.sub(r'([a-z0-9])\1+', r'\1', text.lower())

    def extract_candidates(self, full_text: str) -> Tuple[Optional[str], Optional[str], List[str]]:
        raw_tokens = [t for t in re.split(r'[\s·•\.\-/]+', full_text) if t]
        if not raw_tokens:
            return None, None, []

        # 1. Identify all set code candidates and their token positions
        set_candidates: List[Tuple[str, int, int]] = []  # (set_code, token_index, base_score)

        for idx, token in enumerate(raw_tokens):
            clean_alpha = re.sub(r'[^A-Za-z0-9!|]', '', token).lower()
            if len(clean_alpha) < 3 or len(clean_alpha) > 5:
                continue
            if self.FF_GAME_TAG_PATTERN.match(clean_alpha):
                continue

            variants = [
                clean_alpha,
                clean_alpha.translate(self.TO_LETTERS),
                self._collapse_repeats(clean_alpha),
                self._collapse_repeats(clean_alpha.translate(self.TO_LETTERS))
            ]

            matched = False
            for v in variants:
                if v in self.valid_editions:
                    set_candidates.append((v, idx, 15))  # Exact set match
                    matched = True
                    break

            if not matched and 3 <= len(clean_alpha) <= 4:
                norm = clean_alpha.translate(self.TO_LETTERS)
                matches = get_close_matches(norm, self.valid_editions, n=1, cutoff=0.7)
                if matches:
                    set_candidates.append((matches[0], idx, 8))  # Fuzzy set match

        # 2. For each set code, search in the lookback window (1-3 tokens prior) for Collector Number & Rarity
        scored_results = []

        for set_code, set_idx, base_score in set_candidates:
            # Look backwards up to 4 tokens before the set code
            window_start = max(0, set_idx - 4)
            window = raw_tokens[window_start:set_idx]

            # Search backwards inside the window: closest to set code first
            for rel_idx in range(len(window) - 1, -1, -1):
                tok = window[rel_idx]
                clean_tok = re.sub(r'[^A-Za-z0-9!|]', '', tok)
                if self.FF_GAME_TAG_PATTERN.match(clean_tok):
                    continue

                num_cand = None
                rarity_cand = None
                score = base_score

                # Pattern A: Merged Rarity + Number (e.g. "C0182", "T0014")
                if len(clean_tok) in (4, 5) and clean_tok[0].lower() in self.RARITY_CODES:
                    digits = clean_tok[1:].translate(self.TO_DIGITS)
                    if digits.isdigit():
                        num_cand = digits
                        rarity_cand = clean_tok[0].lower()
                        score += 20

                # Pattern B: Standalone number (e.g. "0182", "0081")
                elif 1 <= len(clean_tok) <= 4:
                    digits = clean_tok.translate(self.TO_DIGITS)
                    if digits.isdigit():
                        num_cand = digits

                        # Check token immediately before for Rarity
                        if rel_idx > 0:
                            prev_tok = re.sub(r'[^A-Za-z]', '', window[rel_idx - 1]).lower()
                            if prev_tok in self.RARITY_CODES:
                                rarity_cand = prev_tok
                                score += 15

                        # Check token immediately after for Rarity (if not the FF tag)
                        if not rarity_cand and rel_idx + 1 < len(window):
                            next_tok = re.sub(r'[^A-Za-z]', '', window[rel_idx + 1]).lower()
                            if next_tok in self.RARITY_CODES:
                                rarity_cand = next_tok
                                score += 12

                if num_cand:
                    if len(num_cand) in (3, 4):
                        score += 10  # Modern collector numbers are 3-4 digits
                    distance = len(window) - rel_idx
                    score -= distance * 2  # Closer to the set code is favored

                    scored_results.append((score, num_cand, rarity_cand, set_code))

        # 3. Pick the highest-scoring candidate combination
        if scored_results:
            scored_results.sort(key=lambda x: x[0], reverse=True)
            _, best_num, best_rarity, best_set = scored_results[0]
        else:
            return None, None, []

        # 4. Route Token ('t') vs Regular sets
        final_editions = []
        if best_rarity == 't':
            token_set = f"t{best_set}" if not best_set.startswith('t') else best_set
            if token_set in self.valid_editions:
                final_editions.append(token_set)
        else:
            if not (len(best_set) == 4 and best_set.startswith('t') and best_set[1:] in self.valid_editions):
                final_editions.append(best_set)

        return best_num, best_rarity, final_editions


