// Tela de login da versão publicada (o Worker valida tudo em /auth/login; ver worker/auth.js).
// Não carrega core.js: aqui ainda não há sessão. Sem cadastro: contas são criadas pelo administrador.

const form = document.getElementById('loginForm');
const msg = document.getElementById('loginMsg');
const btn = document.getElementById('loginBtn');

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
        msg.textContent = 'Informe CPF ou e-mail e a senha.';
        return;
    }
    btn.disabled = true;
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
        msg.textContent = data.message || `Não foi possível entrar (HTTP ${response.status}).`;
        form.password.value = '';
        form.password.focus();
    } catch (e) {
        msg.textContent = 'Sem conexão com o servidor. Tente de novo.';
    } finally {
        btn.disabled = false;
    }
});
