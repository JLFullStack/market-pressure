// Gráficos comuns às páginas que os têm (Backtest e Carteira): o tema do Chart.js e o gráfico de
// preço (Lightweight Charts) com médias, marcadores e legenda. Script clássico, carregado depois
// de core.js e animacao.js, e depois das bibliotecas (CDN).

//#region Tema dos gráficos (Chart.js)
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
// Visual comum dos gráficos (Carteira, Backtest): grade, tooltip escuro e animação de entrada.
const CHART_GRID = 'rgba(120, 150, 210, 0.1)';
const CHART_ENTER = { duration: 900, easing: 'easeOutQuart' };
const chartTooltip = () => ({
    backgroundColor: 'rgba(10, 16, 32, 0.95)', borderColor: cssVar('--border-strong'), borderWidth: 1,
    titleColor: cssVar('--text-color'), bodyColor: cssVar('--text-color'), padding: 12, cornerRadius: 10,
    titleFont: { weight: '700' }, caretSize: 6,
});
// Preenchimento em degradê vertical (cor cheia em cima, alpha embaixo) para barras e áreas.
const verticalGradient = (color, topAlpha = 1, bottomAlpha = 0.45) => (ctx) => {
    const { chart } = ctx;
    if (!chart.chartArea) return color;
    const { top, bottom } = chart.chartArea;
    const g = chart.ctx.createLinearGradient(0, top, 0, bottom);
    g.addColorStop(0, hexAlpha(color, topAlpha));
    g.addColorStop(1, hexAlpha(color, bottomAlpha));
    return g;
};
// Mesmo degradê, na horizontal (barras deitadas): mais transparente à esquerda.
const horizontalGradient = (color, leftAlpha = 0.5, rightAlpha = 1) => (ctx) => {
    const { chart } = ctx;
    if (!chart.chartArea) return color;
    const { left, right } = chart.chartArea;
    const g = chart.ctx.createLinearGradient(left, 0, right, 0);
    g.addColorStop(0, hexAlpha(color, leftAlpha));
    g.addColorStop(1, hexAlpha(color, rightAlpha));
    return g;
};
const hexAlpha = (hex, a) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};
//#endregion

//#region Gráfico de preço (Lightweight Charts)
// Usado pelo Backtest e pelo card "Operações no gráfico" da Carteira (páginas que carregam o
// Lightweight Charts). Cores sobre o fundo #0e1527 do painel; médias como no TradingView do
// usuário: MM20 laranja, MM50 azul, MM200 roxa.

const COLORS = {
    up: '#3ddc97', down: '#ff6b6b', close: '#dce5f5',
    mm20: '#ffb057', mm50: '#5aa2ff', mm200: '#a78bfa',
    buy: '#3ddc97', partial: '#ffc65c', exit: '#ff6b6b', target: '#ffc65c',
    profit: '#5aa2ff', profitB: '#ffb057', zero: '#8a99b6',
    grid: 'rgba(120, 150, 210, 0.08)', border: '#1f2b46', text: '#dce5f5', panel: '#0e1527', crosshair: '#5aa2ff'
};
const MA_LABEL = { mm20: 'MM20', mm50: 'MM50', mm200: 'MM200' };
// Marcador de cada evento (o rótulo, ex. "C3" ou "V1–4", vem pronto do servidor). sell = venda
// lançada na carteira.
const MARKER = {
    buy: { position: 'belowBar', shape: 'arrowUp', color: COLORS.buy },
    partial: { position: 'aboveBar', shape: 'circle', color: COLORS.partial },
    exit: { position: 'aboveBar', shape: 'arrowDown', color: COLORS.exit },
    sell: { position: 'aboveBar', shape: 'arrowDown', color: COLORS.exit }
};
const CHART_KEY = 'marketpressure_backtest_grafico'; // "candles" | "line" (Backtest e Carteira)

// O tamanho vem do contêiner (.bt-chart no CSS): autoSize acompanha a largura.
function chartOptions() {
    const line = { color: COLORS.crosshair, width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#2f6bff' };
    return {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8a99b6', fontSize: 12,
                  fontFamily: getComputedStyle(document.body).fontFamily },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: line, horzLine: line },
        localization: { locale: 'pt-BR', priceFormatter: (p) => fmtNum(p, 2) },
        autoSize: true
    };
}

// Gráfico de preço com candles, linha de fechamento e as médias (só uma das duas séries de preço
// fica visível). Devolve o gráfico e as séries.
function priceChart(el, d) {
    const chart = LightweightCharts.createChart(el, chartOptions());
    const candles = chart.addCandlestickSeries({
        upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down
    });
    candles.setData(d.candles);
    const line = chart.addLineSeries({ color: COLORS.close, lineWidth: 2, priceLineVisible: false });
    line.setData(d.candles.map(c => ({ time: c.time, value: c.close })));
    for (const k of Object.keys(MA_LABEL)) {
        const ma = chart.addLineSeries({ color: COLORS[k], lineWidth: k === 'mm200' ? 2 : 1, priceLineVisible: false,
                                         lastValueVisible: false, crosshairMarkerVisible: false });
        ma.setData(d.ma[k]);
    }
    return { chart, candles, line };
}

// Candles (type "candles") ou linha; os marcadores ficam na série visível.
function showPriceType(series, markers, type) {
    if (!series?.candles) return;
    const candles = type === 'candles';
    series.candles.applyOptions({ visible: candles });
    series.line.applyOptions({ visible: !candles });
    const list = (markers || []).map(m => ({ time: m.time, text: m.text, ...MARKER[m.kind] }));
    series.candles.setMarkers(candles ? list : []);
    series.line.setMarkers(candles ? [] : list);
}

// Legenda das médias e dos marcadores: items = [[cor, texto], ...] depois das médias.
function priceLegend(items) {
    return [
        ...Object.entries(MA_LABEL).map(([k, label]) => `<span><i class="swatch-line" style="background:${COLORS[k]}"></i>${label}</span>`),
        ...items.map(([color, text]) => `<span><i class="swatch-dot" style="background:${color}"></i>${text}</span>`)
    ].join('');
}
//#endregion
