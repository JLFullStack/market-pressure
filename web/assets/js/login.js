// Tela de login da versão publicada (o Worker valida tudo em /auth/login; ver worker/auth.js).
// Não carrega core.js: aqui ainda não há sessão. Sem cadastro: contas são criadas pelo administrador.

const form = document.getElementById('loginForm');
const msg = document.getElementById('loginMsg');
const btn = document.getElementById('loginBtn');
const toggle = document.getElementById('pwToggle');

// Mostrar/ocultar a senha.
toggle.addEventListener('click', () => {
    const show = form.password.type === 'password';
    form.password.type = show ? 'text' : 'password';
    toggle.setAttribute('aria-pressed', String(show));
    toggle.setAttribute('aria-label', show ? 'Ocultar senha' : 'Mostrar senha');
    form.password.focus();
});

// Erro: o cartão balança (a mensagem já está no aviso abaixo do botão).
function fail(text) {
    msg.textContent = text;
    form.classList.remove('shake');
    void form.offsetWidth;
    form.classList.add('shake');
}

// Volta para a página pedida antes do login; só caminhos do próprio site. O redirecionamento
// do Worker não vê o hash (sub-aba da carteira), mas o navegador o mantém nesta URL.
function nextPage() {
    const next = new URLSearchParams(location.search).get('next') || '/';
    if (!next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return '/';
    return next.includes('#') ? next : next + location.hash;
}

form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const login = form.login.value.trim();
    const password = form.password.value;
    if (!login || !password) {
        fail('Informe CPF ou e-mail e a senha.');
        return;
    }
    btn.disabled = true;
    btn.classList.add('loading');
    msg.textContent = '';
    try {
        const response = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ login, password }),
        });
        const data = await response.json().catch(() => ({}));
        if (response.ok) {
            location.replace(nextPage());
            return;
        }
        fail(data.message || `Não foi possível entrar (HTTP ${response.status}).`);
        form.password.value = '';
        form.password.focus();
    } catch (e) {
        fail('Sem conexão com o servidor. Tente de novo.');
    } finally {
        btn.disabled = false;
        btn.classList.remove('loading');
    }
});
