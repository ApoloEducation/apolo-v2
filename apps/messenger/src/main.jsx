import { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API = `${window.location.protocol}//${window.location.hostname}:8000`;

function App() {
  const [schools, setSchools] = useState([]);
  const [school, setSchool] = useState(null);
  const [guardians, setGuardians] = useState([]);
  const [guardian, setGuardian] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const bottom = useRef(null);

  useEffect(() => { fetch(`${API}/v1/schools`).then(r => r.json()).then(setSchools).catch(() => setSchools([])); }, []);
  useEffect(() => { if (schools.length === 1) setSchool(schools[0]); }, [schools]);
  useEffect(() => {
    setGuardians([]);
    if (school) fetch(`${API}/v1/schools/${school.id}/guardians`).then(r => r.json()).then(setGuardians).catch(() => setGuardians([]));
  }, [school]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  useEffect(() => {
    if (!guardian) return;
    fetch(`${API}/v1/history/${school.id}/${guardian.id}`).then(r => r.json()).then(setMessages).catch(() => setMessages([]));
    const socket = new WebSocket(`${API.replace(/^http/, 'ws')}/ws/${school.id}/${guardian.id}`);
    socket.onmessage = event => setMessages(old => [...old, JSON.parse(event.data)]);
    const heartbeat = setInterval(() => socket.readyState === 1 && socket.send('ping'), 25000);
    return () => { clearInterval(heartbeat); socket.close(); };
  }, [guardian]);

  async function send(event) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || busy) return;
    setDraft(''); setBusy(true);
    try {
      const result = await fetch(`${API}/v1/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ school_id: school.id, guardian_id: guardian.id, guardian_name: guardian.name, content }) });
      if (!result.ok) throw new Error((await result.json()).detail || 'Não foi possível enviar a mensagem.');
    } catch (error) {
      setMessages(old => [...old, { sender: 'system', content: error.message }]);
    } finally { setBusy(false); }
  }

  if (!school) return <Schools schools={schools} choose={setSchool} />;
  if (!guardian) return <Welcome school={school} guardians={guardians} choose={setGuardian} back={schools.length > 1 ? () => setSchool(null) : null} />;
  return <main className="shell">
    <header className="chat-header"><button className="back" onClick={() => setGuardian(null)}>‹</button><div className="avatar">{guardian.name[0]}</div><div><strong>{school.name}</strong><small>Atendimento escolar</small></div><span className="secure">🔒 Ambiente de demonstração</span></header>
    <section className="conversation">
      <div className="day">HOJE</div>
      {messages.map((message, index) => <Bubble key={`${message.created_at || ''}-${index}`} message={message} />)}
      {busy && <div className="typing">Assistente consultando informações da escola…</div>}
      <div ref={bottom} />
    </section>
    <form className="composer" onSubmit={send}><input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Digite uma mensagem" maxLength="2000" autoFocus /><button type="submit" disabled={!draft.trim() || busy} aria-label="Enviar">➤</button></form>
  </main>;
}

function Schools({ schools, choose }) {
  return <main className="welcome"><section className="welcome-card"><div className="school-mark">✦</div><h1>Apolo</h1><p>Escolha a escola para iniciar o teste.</p><div className="guardian-list">{schools.map(item => <button key={item.id} onClick={() => choose(item)}><span className="avatar">{item.name[0]}</span>{item.name}<small>Escola de demonstração</small></button>)}</div>{!schools.length && <p className="loading">Preparando os dados…</p>}</section></main>;
}

function Welcome({ school, guardians, choose, back }) {
  return <main className="welcome"><section className="welcome-card"><div className="school-mark">✦</div><h1>{school.name}</h1>{back && <button className="back-link" onClick={back}>‹ Trocar de escola</button>}<p>Escolha um responsável fictício para iniciar o teste.</p><div className="guardian-list">{guardians.map(item => <button key={item.id} onClick={() => choose(item)}><span className="avatar">{item.name[0]}</span>{item.name}<small>{item.students?.length ? `Filho(a): ${item.students.join(', ')}` : 'Perfil de demonstração'}</small></button>)}</div>{!guardians.length && <p className="loading">Preparando os dados da escola…</p>}</section></main>;
}

function Bubble({ message }) {
  const who = message.sender === 'guardian' ? 'outgoing' : message.sender === 'system' ? 'system' : 'incoming';
  const label = message.sender === 'human' ? 'Atendente' : message.sender === 'agent' ? 'Assistente Escolar' : null;
  return <article className={`bubble ${who}`}>{label && <small>{label}</small>}<span>{message.content}</span></article>;
}

createRoot(document.getElementById('root')).render(<App />);
