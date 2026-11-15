"""Session-local bounded reuse and view slicing. Never changes analysis limits."""
from collections import OrderedDict
from spc_access import day_guard
import hashlib
import io
import json
import sys
import numpy as np
import pandas as pd
from spc_core import load_data_df, run_full_analysis


def estimated_bytes(value, seen=None):
    """Conservative resident-size accounting, including array views and containers."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, np.ndarray):
        return size + value.nbytes
    if isinstance(value, pd.DataFrame):
        return size + int(value.memory_usage(deep=True).sum())
    if isinstance(value, dict):
        return size + sum(estimated_bytes(k, seen) + estimated_bytes(v, seen) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        if value and all(isinstance(v, (str, int, float, np.integer)) for v in value):
            return size + sum(sys.getsizeof(v) for v in value)
        return size + sum(estimated_bytes(v, seen) for v in value)
    return size


class BoundedCache:
    def __init__(self, max_entries=3, max_bytes=256 * 1024**2):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key):
        entry = self.entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        self.entries.move_to_end(key)
        return entry[0]

    def put(self, key, value):
        size = estimated_bytes(value)
        if key in self.entries:
            self.bytes -= self.entries.pop(key)[1]
        if size > self.max_bytes:
            return value
        while self.entries and (len(self.entries) >= self.max_entries or self.bytes + size > self.max_bytes):
            self.bytes -= self.entries.popitem(last=False)[1][1]
        self.entries[key] = value, size
        self.bytes += size
        return value

    def stats(self):
        return dict(entries=len(self.entries), bytes=self.bytes, hits=self.hits, misses=self.misses,
                    max_bytes=self.max_bytes, max_entries=self.max_entries)


def content_bytes(value):
    if value is None:
        return b''
    if isinstance(value, bytes):
        return value
    if hasattr(value, 'getvalue'):
        return value.getvalue()
    with open(value, 'rb') as handle:
        return handle.read()


def analysis_key(kwargs):
    fields = dict(kwargs)
    fields.pop('progress_callback', None)
    for name in ('data_bytes', 'spec_bytes'):
        fields[name] = hashlib.sha256(content_bytes(fields.get(name))).hexdigest()
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def cached_analysis(cache, **kwargs):
    kwargs.setdefault('detail_level', 'core')
    key = ('analysis', analysis_key(kwargs))
    result = cache.get(key)
    if result is None:
        result = cache.put(key, run_full_analysis(**kwargs))
    return result


@day_guard
def ui_analysis(state, **kwargs):
    """Reuse the currently displayed result even when it is too large to cache.

    Only signatures are stored here; the working result already belongs to the UI.
    A full result can satisfy a core request, but never the other way around.
    """
    level = kwargs.get('detail_level', 'core')
    signature = analysis_key(dict(kwargs, detail_level='core'))
    old = state.get('_displayed_signature')
    results = state.get('results')
    if old == signature and results and (level == 'core' or results[0].get('detail_level') == 'full'):
        state['_active_result_reused'] = True
        return results, state['summary_df']
    state['_active_result_reused'] = False
    result = cached_analysis(state['_bounded_cache'], **kwargs)
    state['_displayed_signature'] = signature
    return result


def cached_frame(cache, value, sheet_name=None):
    data = content_bytes(value)
    key = ('frame', hashlib.sha256(data).hexdigest(), sheet_name)
    frame = cache.get(key)
    if frame is None:
        frame = cache.put(key, load_data_df(io.BytesIO(data), sheet_name))
    return frame


def signal_indices(it):
    means, ranges = np.asarray(it['means']), np.asarray(it['ranges'])
    mask = (means > it['ucl']) | (means < it['lcl'])
    for indices in it['ksq_idx'].values():
        if indices:
            mask[np.fromiter(indices, dtype=int)] = True
    rbad = np.flatnonzero((ranges > it['r_ucl']) | (ranges < it['r_lcl']))
    mask[rbad + (1 if it['is_xmr'] else 0)] = True
    return np.flatnonzero(mask)


def chart_window(it, page=0, page_size=1000):
    total = len(it['means'])
    start = min(max(0, int(page)) * page_size, max(0, total - 1))
    end = min(total, start + page_size)
    # Include one context point for the MR linking the preceding and first visible point.
    offset = start - 1 if it['is_xmr'] and start else start
    rstart = offset
    rend = end - 1 if it['is_xmr'] else end
    window = dict(it)
    window.update(means=it['means'][offset:end], ranges=it['ranges'][rstart:rend],
                  window_offset=offset, window_start=start, window_end=end, total_points=total)
    window['x_labels'] = (it.get('x_labels') or [])[offset:end] or [str(i + 1) for i in range(offset, end)]
    window['r_labels'] = (it.get('r_labels') or [])[rstart:rend] or [str(i + 1 + int(it['is_xmr'])) for i in range(rstart, rend)]
    window['ksq_idx'] = {k: {i - offset for i in indices if offset <= i < end} for k, indices in it['ksq_idx'].items()}
    for key in ('baseline_display_count', 'monitor_start_index'):
        window[key] = max(0, min(end - offset, it.get(key, 0) - offset))
    for key in ('baseline_r_display_count', 'monitor_r_start_index'):
        window[key] = max(0, min(rend - rstart, it.get(key, 0) - rstart))
    return window
