// Shared by every staff page: navigation, school selection and API calls. No sign-in: the panel is local-only.
const Apolo = (() => {
  const NAV = [['/admin', 'Início'], ['/admin/responsaveis', 'Responsáveis'], ['/admin/avisos', 'Avisos'], ['/admin/frequencia', 'Frequência'], ['/admin/notas', 'Notas'], ['/admin/banco', 'Banco de dados']];
  let school = '';

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
    const headers = body === undefined ? {} : { 'Content-Type': 'application/json' };
    const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
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

  // Resolves once the school is known.
  async function start() {
    const schools = await fetch('/schools').then(response => response.json());
    const wanted = new URLSearchParams(location.search).get('school');
    school = schools.some(item => item.id === wanted) ? wanted : schools[0].id;
    renderTopbar(schools);
    return school;
  }

  // Warns before leaving a page with unsaved work.
  let hasUnsavedWork = () => false;
  window.addEventListener('beforeunload', event => { if (hasUnsavedWork()) event.preventDefault(); });
  const guardUnsaved = check => { hasUnsavedWork = check; };

  return { api, schoolApi, el, href, today, formatDate, toast, start, guardUnsaved };
})();
