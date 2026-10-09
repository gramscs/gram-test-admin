(function () {
    'use strict';
    const period = document.getElementById('report-period');
    function dateVisibility() {
        document.querySelectorAll('.custom-date').forEach(label => {
            label.hidden = period.value !== 'custom';
            label.querySelector('input').required = period.value === 'custom';
        });
    }
    if (period) { period.addEventListener('change', dateVisibility); dateVisibility(); }
    const present = document.getElementById('presentation-toggle');
    function presentation(enabled) {
        document.body.classList.toggle('presentation-mode', enabled);
        present.setAttribute('aria-pressed', String(enabled));
        present.textContent = enabled ? 'Exit presentation' : 'Present';
    }
    if (present) {
        present.addEventListener('click', () => presentation(!document.body.classList.contains('presentation-mode')));
        document.addEventListener('keydown', event => { if (event.key === 'Escape') presentation(false); });
    }
    const source = document.getElementById('report-chart-data');
    if (!source) return;
    const data = JSON.parse(source.textContent);
    const ns = 'http://www.w3.org/2000/svg';
    function svg(tag, attributes, text) {
        const node = document.createElementNS(ns, tag);
        Object.entries(attributes || {}).forEach(([name,value]) => node.setAttribute(name,value));
        if (text !== undefined) node.textContent = text;
        return node;
    }
    function empty(container, text) {
        const node = document.createElement('p'); node.className = 'chart-empty'; node.textContent = text;
        container.append(node);
    }
    const trendContainer = document.getElementById('pickup-chart');
    if (!data.trend.length) empty(trendContainer, 'No valid pickup dates in this view. Add dates to see activity.');
    else {
        const chart = svg('svg', {viewBox:'0 0 640 240', role:'img', 'aria-label':`Pickup volume, ${data.grain} shipment counts. Expand View chart data for all values.`});
        const max = Math.max(1, ...data.trend.map(item => item.count));
        const high = Math.max(1, Math.ceil(max / 4)) * 4;
        const points = data.trend.map((item,index) => [48+index/Math.max(data.trend.length-1,1)*568, 194-item.count/high*159]);
        for (let i=0; i<=4; i++) {
            const y=194-i*159/4;
            chart.append(svg('line',{x1:48,y1:y,x2:616,y2:y,class:'chart-gridline'}));
            chart.append(svg('text',{x:35,y:y+4,'text-anchor':'end',class:'chart-axis'},String(Math.round(high*i/4))));
        }
        const line=points.map(([x,y],i) => `${i?'L':'M'}${x},${y}`).join(' ');
        chart.append(svg('path',{d:`${line} L${points[points.length-1][0]},194 L48,194 Z`,class:'chart-area'}));
        chart.append(svg('path',{d:line,class:'chart-line'}));
        points.forEach(([x,y],index) => {
            // For long series, the transparent focus points keep precise values available.
            const item=data.trend[index];
            const point=svg('circle',{cx:x,cy:y,r:data.trend.length>90?3:4,class:'chart-point',tabindex:'0','aria-label':`${item.label}: ${item.count} shipments`});
            point.append(svg('title',{},`${item.label} · ${item.count} shipments`));
            chart.append(point);
        });
        [...new Set([0,Math.floor((data.trend.length-1)/2),data.trend.length-1])].forEach(index => {
            chart.append(svg('text',{x:points[index][0],y:222,'text-anchor':index===0?'start':index===data.trend.length-1?'end':'middle',class:'chart-axis'},data.trend[index].label));
        });
        trendContainer.append(chart);
    }
    const colors={'Pickup Scheduled':'#d3a14b','In Transit':'#6696ef','Out for Delivery':'#9774df','Delivered':'#358e71','No status':'#9ba7b0','Other':'#de7980'};
    const total=data.metrics.total;
    const donut=svg('svg',{viewBox:'0 0 210 210',role:'img','aria-label':`Current shipment status. ${total} shipments; delivered share ${data.metrics.delivered_share} percent. See legend for counts.`});
    const radius=78, circumference=2*Math.PI*radius;
    donut.append(svg('circle',{cx:105,cy:105,r:radius,fill:'none','stroke-width':18,class:'donut-track'}));
    let offset=0;
    data.statuses.forEach(item => {
        const length=item.count/total*circumference;
        const arc=svg('circle',{cx:105,cy:105,r:radius,fill:'none',stroke:colors[item.label],'stroke-width':18,'stroke-dasharray':`${length} ${circumference-length}`,'stroke-dashoffset':-offset,transform:'rotate(-90 105 105)',tabindex:'0','aria-label':`${item.label}: ${item.count} shipments`});
        arc.append(svg('title',{},`${item.label} · ${item.count} shipments (${(item.count/total*100).toFixed(1)}%)`));
        donut.append(arc);offset+=length;
        const row=document.createElement('div');row.className='legend-row';
        const dot=document.createElement('span');dot.className='legend-dot';dot.style.backgroundColor=colors[item.label];dot.setAttribute('aria-hidden','true');
        const label=document.createElement('span');label.textContent=item.label;
        const percentage=document.createElement('small');percentage.textContent=`${(item.count/total*100).toFixed(1)}% of shipments`;label.append(percentage);
        const count=document.createElement('strong');count.textContent=item.count;
        row.append(dot,label,count);document.getElementById('status-legend').append(row);
    });
    donut.append(svg('text',{x:105,y:105,'text-anchor':'middle',class:'donut-total'},total.toLocaleString()));
    donut.append(svg('text',{x:105,y:125,'text-anchor':'middle',class:'donut-caption'},'SHIPMENTS'));
    document.getElementById('status-chart').append(donut);
    if(!total) empty(document.getElementById('status-legend'),'No shipments in this view.');
})();
