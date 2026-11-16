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

  // Highlight where you are in the bar
  var path=location.pathname;
  document.querySelectorAll('.menus>a,.posbar nav a').forEach(function(a){var h=a.getAttribute('href');if(h&&h!=='/'&&(path===h||(h.length>1&&path.indexOf(h)===0&&h.split('/').length>3)))a.classList.add('on');});
  document.querySelectorAll('.dd').forEach(function(d){if(d.querySelector('.dd-menu a[href="'+path+'"]')||[].some.call(d.querySelectorAll('.dd-menu a'),function(a){var h=a.getAttribute('href');return h&&h.split('/').length>3&&path.indexOf(h)===0;}))d.classList.add('on');});

  // Light / dark switch, saved per browser
  (function(){
    var root=document.documentElement;
    function cur(){return root.getAttribute('data-theme')==='light'?'light':'dark';}
    function apply(t,save){
      root.setAttribute('data-theme',t);
      if(save){try{localStorage.setItem('alifaris-theme',t);}catch(e){}}
      var m=document.querySelector('meta[name=theme-color]');if(m)m.setAttribute('content',t==='light'?'#f2f3f5':'#0a0b0d');
      document.querySelectorAll('.theme-toggle').forEach(function(b){b.setAttribute('aria-pressed',t==='light');var l=t==='light'?'Switch to dark mode':'Switch to light mode';b.title=l;b.setAttribute('aria-label',l);});
      window.dispatchEvent(new Event('themechange'));
    }
    var b=document.createElement('button');b.type='button';b.className='theme-toggle';
    b.innerHTML='<svg class="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg><svg class="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
    var anchor=document.querySelector('.topbar-right .user, .invbar .user')||document.querySelector('.posbar .who');
    if(anchor&&anchor.parentNode)anchor.parentNode.insertBefore(b,anchor);else{b.classList.add('floating');document.body.appendChild(b);}
    b.addEventListener('click',function(){apply(cur()==='light'?'dark':'light',true);});
    apply(cur(),false);
    window.addEventListener('storage',function(e){if(e.key==='alifaris-theme'&&(e.newValue==='light'||e.newValue==='dark'))apply(e.newValue,false);});
  })();
})();
