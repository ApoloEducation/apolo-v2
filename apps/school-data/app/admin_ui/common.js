// Shared by every staff page: sign-in, navigation, school selection and API calls.
const Apolo = (() => {
  const TOKEN_KEY = 'apolo-admin-token';
  const NAV = [['/admin', 'Início'], ['/admin/avisos', 'Avisos'], ['/admin/frequencia', 'Frequência'], ['/admin/notas', 'Notas'], ['/admin/banco', 'Banco de dados']];
  let memoryToken = '';
  let school = '';

  // sessionStorage keeps the token across pages until the tab closes; it can be unavailable.
  const readToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || memoryToken; } catch { return memoryToken; } };
  const writeToken = value => { memoryToken = value; try { value ? sessionStorage.setItem(TOKEN_KEY, value) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* memory only */ } };

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === 'class') node.className = value;
      else if (key.startsWith('on') || key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    node.append(...children.filter(child => child !== null && child !== undefined));
    return node;
  }

  async function api(path, method = 'GET', body) {
    const headers = { Authorization: `Bearer ${readToken()}` };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (response.status === 401) { writeToken(''); location.reload(); throw new Error('Token inválido'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const { detail } = data;
      throw new Error(Array.isArray(detail) ? detail[0]?.msg || 'Dados inválidos' : detail || `Erro ${response.status}`);
    }
    return data;
  }

  const schoolApi = (path, method, body) => api(`/admin/schools/${encodeURIComponent(school)}${path}`, method, body);
  const href = path => `${path}?school=${encodeURIComponent(school)}`;

  function today() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  }
  const formatDate = iso => iso.split('-').reverse().join('/');

  let toastTimer;
  function toast(message) {
    document.querySelector('.toast')?.remove();
    const node = el('div', { class: 'toast', role: 'status' }, message);
    document.body.append(node);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.remove(), 3500);
  }

  function signIn() {
    return new Promise(resolve => {
      const input = el('input', { type: 'password', required: true, autocomplete: 'current-password', id: 'signin-token' });
      const error = el('div', { class: 'error', role: 'alert' });
      const form = el('form', {
        onsubmit: async event => {
          event.preventDefault();
          const response = await fetch('/admin/tables', { headers: { Authorization: `Bearer ${input.value}` } });
          if (!response.ok) { error.textContent = 'Token inválido.'; input.select(); return; }
          writeToken(input.value);
          dialog.close();
          dialog.remove();
          resolve();
        },
      }, el('h2', {}, 'Entrar no painel'), el('label', { htmlFor: 'signin-token' }, 'Token de acesso', input), error,
      el('div', { class: 'dialog-actions' }, el('button', { type: 'submit', class: 'primary' }, 'Entrar')));
      const dialog = el('dialog', { oncancel: event => event.preventDefault() }, form);
      document.body.append(dialog);
      dialog.showModal();
    });
  }

  function renderTopbar(schools) {
    const links = NAV.map(([path, label]) => {
      const link = el('a', { href: href(path) }, label);
      if (path === location.pathname.replace(/\/$/, '')) link.setAttribute('aria-current', 'page');
      return link;
    });
    const picker = el('select', {
      'aria-label': 'Escola',
      onchange: event => { location.href = `${location.pathname}?school=${encodeURIComponent(event.target.value)}`; },
    }, ...schools.map(item => new Option(item.name, item.id, false, item.id === school)));
    document.body.prepend(el('header', { class: 'topbar' }, el('a', { class: 'brand', href: href('/admin') }, 'Apolo'), el('nav', { 'aria-label': 'Principal' }, ...links), schools.length > 1 ? picker : null));
  }

  // Resolves once the user is signed in and the school is known.
  async function start() {
    const schools = await fetch('/schools').then(response => response.json());
    const wanted = new URLSearchParams(location.search).get('school');
    school = schools.some(item => item.id === wanted) ? wanted : schools[0].id;
    const check = readToken() ? await fetch('/admin/tables', { headers: { Authorization: `Bearer ${readToken()}` } }) : null;
    if (!check?.ok) { writeToken(''); await signIn(); }
    renderTopbar(schools);
    return school;
  }

  // Warns before leaving a page with unsaved work.
  let hasUnsavedWork = () => false;
  window.addEventListener('beforeunload', event => { if (hasUnsavedWork()) event.preventDefault(); });
  const guardUnsaved = check => { hasUnsavedWork = check; };

  return { api, schoolApi, el, href, today, formatDate, toast, start, guardUnsaved };
})();
