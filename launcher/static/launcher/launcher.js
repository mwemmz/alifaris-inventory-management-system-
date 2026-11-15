(function(){
  var x=document.getElementById('notice-x'),n=document.getElementById('notice');
  if(x&&n)x.addEventListener('click',function(){n.remove();});
  var b=document.getElementById('user-btn'),m=document.getElementById('user-menu');
  if(b&&m){
    b.addEventListener('click',function(e){e.stopPropagation();m.hidden=!m.hidden;});
    document.addEventListener('click',function(e){if(!m.contains(e.target))m.hidden=true;});
  }

  // Alerts and Activity panels
  var url='/notifications/',cache=null,pending=null;
  function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e;}
  function load(){
    if(pending)return pending;
    pending=fetch(url,{credentials:'same-origin',headers:{'Accept':'application/json'}}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(d){cache=d;paint(d);return d;}).catch(function(){
      ['alert-list','activity-list'].forEach(function(id){var u=document.getElementById(id);if(u){u.textContent='';u.appendChild(el('li','empty','Could not load. Check your connection.'));}});
    }).then(function(){pending=null;});
    return pending;
  }
  function paint(d){
    var bd=document.getElementById('alert-badge');
    if(bd){bd.textContent=d.alerts.length;bd.hidden=!d.alerts.length;}
    var al=document.getElementById('alert-list');
    if(al){al.textContent='';
      if(!d.alerts.length)al.appendChild(el('li','empty','All clear. Nothing needs attention.'));
      d.alerts.forEach(function(a){var li=el('li');var l=el('a','lvl-'+a.level,a.text);l.href=a.url;li.appendChild(l);al.appendChild(li);});}
    var ac=document.getElementById('activity-list');
    if(ac){ac.textContent='';
      if(!d.activity.length)ac.appendChild(el('li','empty','No recent activity yet.'));
      d.activity.forEach(function(a){var li=el('li');li.appendChild(el('span','',a.text));li.appendChild(el('time','',a.when));ac.appendChild(li);});}
    var more=document.getElementById('activity-more');
    if(more){if(d.activity_url){more.href=d.activity_url;more.hidden=false;}else more.hidden=true;}
  }
  var btns=document.querySelectorAll('[data-pop]');
  function closeAll(except){btns.forEach(function(b){if(b===except)return;b.setAttribute('aria-expanded','false');var p=document.getElementById('pop-'+b.getAttribute('data-pop'));if(p)p.hidden=true;});}
  btns.forEach(function(b){
    b.addEventListener('click',function(e){
      e.stopPropagation();
      var p=document.getElementById('pop-'+b.getAttribute('data-pop'));if(!p)return;
      var open=p.hidden;closeAll(b);p.hidden=!open;b.setAttribute('aria-expanded',open?'true':'false');
      if(open)load();
    });
  });
  document.addEventListener('click',function(e){if(!e.target.closest('.pop'))closeAll();});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')closeAll();});
  if(btns.length)load();
})();
