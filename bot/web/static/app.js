'use strict';
const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const $ = id => document.getElementById(id);
const state = {orders: [], operations: [], correction: null, requestId: crypto.randomUUID(), busy: false};
const paymentLabels = {unpaid:'При получении',pending:'Ожидает оплаты',paid:'Оплачен',refunded:'Возвращён',canceled:'Платёж отменён'};
const orderLabels = {new:'Новый',ready:'Готов к выдаче',completed:'Выдан',cancelled:'Отменён'};
const methodLabels = {cash_on_pickup:'При получении',yookassa_test:'ЮKassa Test',demo:'Демонстрация без платёжного провайдера'};
const opLabels = {receipt:'Поступление',refund:'Полный возврат',expense:'Расход'};
const rub = value => value === null ? 'Не определён' : new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB'}).format(value / 100);
const moscowDate = (value = new Date()) => new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(value);
const dateText = value => value ? new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'}).format(new Date(value.replace(' ','T')+'Z')) : '—';
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node; }
function error(text) { $('error').textContent=text; $('error').hidden=false; if($('order-dialog').open){$('dialog-error').textContent=text;$('dialog-error').hidden=false;} }
function notice(text) { $('notice').textContent=text; $('notice').hidden=false; }
function askConfirmation(text) {
  return new Promise(resolve=>{
    const dialog=$('confirm-dialog');$('confirm-text').textContent=text;
    const finish=value=>{dialog.close();resolve(value);};
    $('confirm-yes').disabled=false;$('confirm-no').disabled=false;
    $('confirm-yes').onclick=()=>finish(true);$('confirm-no').onclick=()=>finish(false);
    dialog.oncancel=e=>{e.preventDefault();finish(false);};dialog.showModal();
  });
}
function button(text, action, cls='outline') { const node=el('button',text,cls); node.type='button'; node.onclick=()=>guard(()=>action(node)); return node; }
function badge(text, status) { return el('span',text,'badge '+status); }
function setTab(name) {document.querySelectorAll('.tab').forEach(n=>n.hidden=n.id!==name);document.querySelectorAll('[data-tab]').forEach(n=>n.classList.toggle('active',n.dataset.tab===name));$('period').hidden=name==='expenses'||name==='orders';}
async function guard(action) {if(state.busy)return;state.busy=true;document.querySelectorAll('button').forEach(n=>n.disabled=true);try{await action();}catch(e){error(e.message);}finally{state.busy=false;document.querySelectorAll('button').forEach(n=>n.disabled=false);}}
async function api(path, data) {
  if(!tg?.initData)throw new Error('Откройте кабинет кнопкой из Telegram-бота под аккаунтом менеджера. В обычном браузере финансовые данные недоступны.');
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),65000);
  try {
    const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{Authorization:'tma '+tg.initData,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),signal:controller.signal});
    const result=await response.json().catch(()=>({error:'Сервер вернул некорректный ответ.'}));
    if(!response.ok)throw new Error(result.error||'Операция не выполнена.');
    return result;
  } catch(e) { if(e.name==='AbortError'||e instanceof TypeError)throw new Error('Нет связи с сервером. Изменения не подтверждены. Проверьте сеть и обновите данные перед повтором.');throw e; }
  finally {clearTimeout(timeout);}
}
const query = () => new URLSearchParams({start:$('start').value,end:$('end').value}).toString();
async function refresh() {
  const [summary,orders,operations]=await Promise.all([api('summary?'+query()),api('orders'),api('operations?'+query())]);
  state.orders=orders;state.operations=operations;
  $('summary-period').textContent=summary.start+' — '+summary.end;
  for(const [id,key] of [['cash-flow','cash_flow'],['result','result'],['receipts','receipts'],['refunds','refunds'],['expenses-total','expenses'],['cost-paid','cost_paid'],['cost-returned','cost_returned']])$(id).textContent=rub(summary[key]);
  $('cost-warning').hidden=!summary.unknown_cost_operations;
  $('cost-warning').textContent='У старых заказов отсутствует историческая себестоимость. Денежный поток точен, учебный результат за этот период не определён.';
  renderOrders();renderOperations();$('error').hidden=true;
}
function renderOrders() {
  const list=$('order-list');list.replaceChildren();
  if(!state.orders.length){list.append(el('div','Пока нет заказов. Оформите первый в боте.','empty'));return;}
  for(const order of state.orders){
    const row=el('article',undefined,'row'), left=el('div'), right=el('div',undefined,'row-side');
    left.append(el('h3','Заказ №'+order.id+' · '+order.customer_name),el('p',dateText(order.created_at)),badge(orderLabels[order.status]||order.status,order.status),badge(paymentLabels[order.payment_status]||order.payment_status,order.payment_status),el('p',methodLabels[order.payment_method]||order.payment_method));
    right.append(el('span',rub(order.total_kopecks),'sum'),button('Открыть',()=>openOrder(order.id)));
    row.append(left,right);list.append(row);
  }
}
function renderOperations() {
  const list=$('operation-list');list.replaceChildren();
  if(!state.operations.length){list.append(el('div','За этот период операций нет. Ожидающий платёж не является поступлением.','empty'));return;}
  for(const op of state.operations){
    const row=el('article',undefined,'row'+(op.cancelled_at?' operation-cancelled':'')),left=el('div'),right=el('div',undefined,'row-side');
    left.append(el('h3',opLabels[op.type]+(op.order_id?' · заказ №'+op.order_id:'')),el('p',op.description),el('p',dateText(op.occurred_at)+' · автор '+op.author_id));
    if(op.external_event_id)left.append(el('p','Событие: '+op.external_event_id));
    if(op.cancelled_at)left.append(badge('Отменён / исправлен','cancelled'));
    if(op.replacement_of)left.append(el('p','Исправление расхода №'+op.replacement_of));
    for(const event of op.history)left.append(el('div',dateText(event.created_at)+' · '+({created:'Создан',corrected:'Исправлен новой записью',cancelled:'Отменён'}[event.action]||event.action)+' · автор '+event.author_id,'history'));
    right.append(el('span',(op.type==='receipt'?'+':'−')+rub(op.amount_kopecks),'sum'));
    if(op.type==='expense'&&!op.cancelled_at){const actions=el('div',undefined,'actions');actions.append(button('Исправить',()=>editExpense(op)),button('Отменить',async()=>{if(!await askConfirmation('Отменить расход №'+op.id+'? Запись останется в истории.'))return;await api('expenses/'+op.id+'/cancel',{});await refresh();notice('Расход отменён. История сохранена.');},'danger'));right.append(actions);}
    row.append(left,right);list.append(row);
  }
}
function line(parent,label,value){const row=el('div',undefined,'detail-line');row.append(el('span',label,'detail-label'),el('span',value));parent.append(row);}
async function openOrder(id) {
  $('dialog-error').hidden=true;
  const order=await api('orders/'+id), detail=$('order-detail');detail.replaceChildren();$('order-title').textContent='Заказ №'+id;
  line(detail,'Покупатель',order.customer_name);line(detail,'Статус',orderLabels[order.status]);line(detail,'Оплата',paymentLabels[order.payment_status]);line(detail,'Способ',methodLabels[order.payment_method]);line(detail,'Сумма',rub(order.total_kopecks));
  detail.append(el('h3','СОСТАВ ЗАКАЗА','detail-heading'));
  for(const item of order.items){line(detail,item.product_name+' × '+item.quantity,rub(item.price_kopecks*item.quantity));line(detail,'Себестоимость на момент заказа',item.cost_kopecks===null?'Не сохранена (старый заказ)':rub(item.cost_kopecks*item.quantity));}
  detail.append(el('h3','ПЛАТЕЖИ','detail-heading'));
  if(!order.payments.length)detail.append(el('p','Подтверждённых учебных платежей пока нет.'));
  for(const p of order.payments){line(detail,'ID платежа',p.id);line(detail,'Состояние',{pending:'Ожидает оплаты',succeeded:'Успешен',canceled:'Отменён'}[p.status]);if(p.provider_payment_id)line(detail,'ID провайдера',p.provider_payment_id);line(detail,'Подтверждён',dateText(p.confirmed_at));}
  for(const refund of order.refunds){
    detail.append(el('h3','ПОЛНЫЙ ВОЗВРАТ','detail-heading'));line(detail,'ID возврата',refund.id);line(detail,'Состояние',{refund_pending:'Ожидает подтверждения',refund_succeeded:'Подтверждён',refund_failed:'Не выполнен'}[refund.status]);
    if(refund.error)detail.append(el('p',refund.error));
    if(refund.status==='refund_pending'){
      const actions=el('div',undefined,'actions');
      if(order.payment_method==='demo'){
        detail.append(el('p','Демонстрация без платёжного провайдера. До следующего подтверждения сводка не меняется.'));
        actions.append(button('Демо: подтвердить возврат',()=>refundAction(id,'refunds/'+refund.id+'/demo',{outcome:'succeeded'})),button('Демо: ошибка возврата',()=>refundAction(id,'refunds/'+refund.id+'/demo',{outcome:'canceled'}),'danger'));
      } else actions.append(button('Проверить у ЮKassa',()=>refundAction(id,'refunds/'+refund.id+'/check',{})));
      detail.append(actions);
    }
  }
  if(order.payment_status==='paid'&&!order.refunds.some(r=>r.status==='refund_pending'||r.status==='refund_succeeded'))detail.append(button('Запросить полный возврат',async()=>{if(!await askConfirmation('Полностью вернуть '+rub(order.total_kopecks)+' по заказу №'+id+'?'))return;await refundAction(id,'orders/'+id+'/refund',{});},'danger'));
  if(!$('order-dialog').open)$('order-dialog').showModal();
}
async function refundAction(orderId,path,data){try{await api(path,data);}catch(e){await openOrder(orderId);throw e;}await refresh();await openOrder(orderId);notice('Данные возврата обновлены. В сводке учитывается только подтверждённый возврат.');}
function resetExpense() {state.correction=null;state.requestId=crypto.randomUUID();$('expense-form').reset();$('expense-date').value=moscowDate();$('expense-title').textContent='Новый расход';$('save-expense').textContent='Сохранить расход';}
function editExpense(op) {state.correction=op.id;state.requestId=crypto.randomUUID();$('category').value=op.category;$('amount').value=(op.amount_kopecks/100).toFixed(2);$('description').value=op.description;$('expense-date').value=moscowDate(new Date(op.occurred_at.replace(' ','T')+'Z'));$('expense-title').textContent='Исправить расход №'+op.id;$('save-expense').textContent='Сохранить исправление';setTab('expenses');}
document.querySelectorAll('[data-tab]').forEach(node=>node.onclick=()=>setTab(node.dataset.tab));
$('period').onsubmit=e=>{e.preventDefault();guard(refresh);};
$('refresh').onclick=()=>guard(refresh);
$('close-order').onclick=()=>$('order-dialog').close();
$('reset-expense').onclick=resetExpense;
$('expense-form').addEventListener('input',()=>{state.requestId=crypto.randomUUID();});
$('expense-form').onsubmit=e=>{e.preventDefault();guard(async()=>{
  const path=state.correction?'expenses/'+state.correction+'/correct':'expenses';
  await api(path,{amount:$('amount').value,category:$('category').value,date:$('expense-date').value,description:$('description').value,request_id:state.requestId});
  resetExpense();await refresh();setTab('operations');notice('Расход сохранён. Сводка пересчитана.');
});};
window.addEventListener('offline',()=>error('Нет сети. Показанные данные могут быть устаревшими; изменения не подтверждены.'));
const today=moscowDate();$('start').value=today.slice(0,7)+'-01';$('end').value=today;$('expense-date').value=today;
guard(async()=>{const meta=await api('meta');for(const [key,name]of Object.entries(meta.categories)){const option=el('option',name);option.value=key;$('category').append(option);}await refresh();});
