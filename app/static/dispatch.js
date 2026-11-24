const status = document.querySelector('#dispatch-message');

async function post(path,body) {
  const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '交班提交失败');
  return data;
}

function bind(selector, submit) {
  const form = document.querySelector(selector);
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    status.textContent = '正在核量…';
    try { await submit(form); }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

bind('#archive', async form => {
  const files = Object.fromEntries(['tickets','receipts','stock'].map(id => [id,document.getElementById(id).value]));
  const values = Object.values(files);
  if (values.some(Boolean) && !values.every(Boolean)) throw new Error('请填全三份 CSV');
  const sample = form.dataset.sample;
  const ack = await post('/api/shifts', values.every(Boolean) ? files : {sample});
  location.assign('/ops/sign?' + new URLSearchParams({sample,batch:ack.batch}));
});

const pick = document.querySelector('#batch');
const hash = document.querySelector('#checksum');
const exportLink = document.querySelector('#export-slip');
const revisions = document.querySelector('#revisions');

function kg(grams) { return (grams/1000).toFixed(3); }

async function loadLineage(batch) {
  if (!revisions || !batch) return;
  const response = await fetch(`/api/shifts/${batch}`);
  if (!response.ok) return;
  const detail = await response.json();
  revisions.replaceChildren(...detail.lineage.revisions.map(rev => {
    const item = document.createElement('li');
    const span = document.createElement('span');
    span.textContent = `第 ${rev.revision} 口径 · 规则 ${rev.rules} · 系数 ${rev.parameters.thermal_coefficient} · 库存差 ${kg(rev.totals.difference_g)} kg`;
    const time = document.createElement('small');
    time.textContent = `${rev.archived_at} 落账 · ${rev.superseded_at ? `适用至 ${rev.superseded_at}` : '现行适用'}${rev.signed ? ' · 已签发' : ' · 未签发'}`;
    item.append(span,time);
    if (rev.batch === detail.lineage.current) item.classList.add('current');
    return item;
  }));
}

pick?.addEventListener('change', () => { hash.value = ''; exportLink.hidden = true; loadLineage(pick.value); });
if (pick?.value) loadLineage(pick.value);

bind('#recheck', async form => {
  const batch = pick.value;
  const result = await post(`/api/shifts/${batch}/recheck`,{sample:form.dataset.sample});
  if (![...pick.options].some(option => option.value === result.batch))
    pick.add(new Option(`${result.batch.slice(0,15)} · ${kg(result.totals.difference_g)} kg`,result.batch));
  pick.value = result.batch;
  pick.dispatchEvent(new Event('change'));
  status.textContent = result.reused ? '该口径结果已在账，复算未新增' : `已按 ${result.rules} 复算落账 · 库存差 ${kg(result.totals.difference_g)} kg`;
});
bind('#signature', async () => {
  const batch = pick.value;
  const result = await post(`/api/shifts/${batch}/sign`,{});
  if (pick.value !== batch) return;
  hash.value = result.checksum;
  exportLink.href = `/api/shifts/${batch}/export`;
  exportLink.hidden = false;
  status.textContent = `证据已签发 · 待复核 ${result.open_items} 票`;
});
