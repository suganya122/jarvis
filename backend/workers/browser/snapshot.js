// Run in the page by pw.py: tags visible interactive elements with short refs and returns
// the page's structure plus trimmed text. Never returns password or card field values.
() => {
  document.querySelectorAll('[data-assistant-ref]').forEach(e => e.removeAttribute('data-assistant-ref'));
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary,'
    + ' [role=button], [role=link], [role=checkbox], [role=radio], [role=textbox],'
    + ' [role=combobox], [contenteditable=true]';
  const buttonTypes = ['submit', 'button', 'reset', 'image'];
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  const name = el => {
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    const by = el.getAttribute('aria-labelledby');
    if (by) { const l = document.getElementById(by); if (l) return clean(l.innerText); }
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    if (el.tagName === 'INPUT' && buttonTypes.includes(el.type)) return clean(el.value || el.alt);
    return clean(el.innerText || el.placeholder || el.title || el.alt || el.getAttribute('name'));
  };
  const kind = (el, tag, type, role) => {
    if (role === 'checkbox' || type === 'checkbox') return 'checkbox';
    if (role === 'radio' || type === 'radio') return 'radio';
    if (tag === 'select' || role === 'combobox') return 'select';
    if (tag === 'a' || role === 'link') return 'link';
    if (tag === 'button' || role === 'button' || tag === 'summary') return 'button';
    if (buttonTypes.includes(type)) return 'button';
    if (tag === 'input' || tag === 'textarea' || role === 'textbox' || el.isContentEditable) return 'textbox';
    return 'other';
  };
  const forms = [...document.forms];
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= 200) break;
    if (!visible(el) || el.disabled) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'button' ? 'submit' : '')).toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const k = kind(el, tag, type, role);
    const ref = 'e' + (out.length + 1);
    el.setAttribute('data-assistant-ref', ref);
    const secret = type === 'password' || /^(cc-|current-password|new-password|one-time-code)/.test(el.autocomplete || '');
    let value = '';
    if (k === 'textbox' && !secret) value = clean(el.value !== undefined ? el.value : el.innerText);
    if (k === 'select' && el.selectedIndex >= 0 && el.options) value = clean(el.options[el.selectedIndex].text);
    out.push({ ref, kind: k, name: name(el), input_type: type, autocomplete: (el.autocomplete || '').toLowerCase(),
      field_name: clean(el.getAttribute('name') || el.id), value,
      options: k === 'select' && el.options ? [...el.options].slice(0, 30).map(o => clean(o.text)) : [],
      checked: (k === 'checkbox' || k === 'radio') ? !!el.checked : null,
      form: el.form ? (el.form.id || 'form' + forms.indexOf(el.form)) : '' });
  }
  return { url: location.href, title: document.title, elements: out,
    text: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim().slice(0, 20000),
    frames: [...document.querySelectorAll('iframe')].map(f => f.src).filter(Boolean) };
}
