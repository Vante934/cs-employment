// Setup variables
let currentView = 'home';
let activeNav = '城市分布';
let activeRole = '前端';
let activeYear = '2023';
let detailDirection = '前端开发';
let detailTab = '介绍';
let detailYear = '2024';

let dashboardChart = null;
let techChart = null;
let salaryChart = null;

// 专业详情介绍内容
const directionIntro = {
    '前端开发': '前端开发是构建用户与互联网产品交互的 "门面工程师"，聚焦 Web 页面、小程序、APP 等终端的视觉呈现与交互体验，核心掌握 HTML/CSS/JavaScript 及 React、Vue 等主流框架，就业场景覆盖全行业，从互联网大厂到中小企业均有大量需求，是计算机专业最易入门、岗位缺口持续稳定的核心方向。',
    '后端开发': '后端开发是各类互联网产品的 "幕后基石"，负责服务器搭建、数据库设计、业务逻辑实现与接口开发，核心使用 Java、Go、Python、C++ 等语言，需具备高并发、高可用的系统设计能力，是大厂技术架构的核心岗位，薪资天花板高，且随着项目经验积累，职业竞争力和抗替代性显著提升。',
    '人工智能': '人工智能是当前计算机领域的前沿方向，以机器学习、深度学习、大模型、计算机视觉（CV）、自然语言处理（NLP）为核心，融合数学、算法与工程实现，主要应用于智能推荐、自动驾驶、人机交互等场景，岗位集中在头部科技企业、AI 独角兽及科研机构，对学历和算法能力要求较高，但薪资与发展潜力均处于行业第一梯队。',
    '数据分析': '数据分析是 "数据时代的翻译官"，通过 SQL、Python、BI 工具挖掘海量数据的商业价值，衔接业务与技术，核心工作包括数据清洗、可视化、建模与决策支持，就业覆盖互联网、金融、电商、制造业等全领域，岗位门槛适中，是跨行业就业的优质选择，且随着企业数字化转型，需求持续增长。',
    '算法工程': '算法工程是 "算法落地的桥梁"，兼顾算法设计与工程实现，核心将 AI 算法、推荐算法、搜索算法等转化为可上线的工业级系统，需掌握算法原理、工程优化与高性能编程，区别于纯研究型算法岗，更侧重落地能力，就业集中在大厂算法团队、短视频 / 电商平台，是技术与业务结合的高薪方向，岗位稀缺性高。'
};

// Navigation
function switchView(viewId, navName = null) {
    document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
    document.getElementById('view-' + viewId).classList.add('active');
    
    document.querySelectorAll('header nav a').forEach(el => el.classList.remove('active'));
    
    if (viewId === 'dashboard' && navName) {
        activeNav = navName;
        document.querySelector(`header nav a[data-nav="${navName}"]`).classList.add('active');
        renderDashboard();
    } else if (viewId === 'detail') {
        renderDetail();
    }
}

document.getElementById('nav-home').addEventListener('click', () => switchView('home'));
document.querySelectorAll('header nav a').forEach(link => {
    link.addEventListener('click', (e) => {
        switchView('dashboard', e.target.dataset.nav);
    });
});

// Carousel
let currentSlide = 0;
const slides = document.querySelector('.carousel-inner');
const indicators = document.querySelectorAll('.carousel-indicator');
const totalSlides = 5;

function updateCarousel() {
    slides.style.transform = `translateX(-${currentSlide * 100}%)`;
    indicators.forEach((ind, i) => {
        ind.classList.toggle('active', i === currentSlide);
    });
}

function nextSlide() {
    currentSlide = (currentSlide + 1) % totalSlides;
    updateCarousel();
}

function prevSlide() {
    currentSlide = (currentSlide - 1 + totalSlides) % totalSlides;
    updateCarousel();
}

function goToSlide(index) {
    currentSlide = index;
    updateCarousel();
}

setInterval(nextSlide, 5000);

// Direction Cards
document.querySelectorAll('.card').forEach(card => {
    card.addEventListener('click', () => {
        detailDirection = card.dataset.dir;
        detailTab = '介绍'; // 默认显示介绍标签
        switchView('detail');
    });
});

// Dashboard Logic
const roleMap = {
    '前端': 'frontend', '后端': 'backend', '人工智能': 'artificial_intelligence',
    '算法': 'ai', '测试': 'backend', '运维': 'backend', '数据分析': 'ai', '产品经理': 'frontend'
};

function renderDashboard() {
    if (!dashboardChart) {
        dashboardChart = echarts.init(document.getElementById('chart-container'));
    }
    
    document.getElementById('dashboard-title').innerText = `${activeYear}年 ${activeRole} ${activeNav}统计`;
    
    const roleKey = roleMap[activeRole] || 'frontend';
    // 根据年份获取对应数据
    const yearData = window.mockData[roleKey] && window.mockData[roleKey][activeYear];
    
    if (!yearData) {
        console.error(`数据缺失：${activeYear}年 ${activeRole} 数据不存在`);
        dashboardChart.clear();
        return;
    }
    
    let option = {};

    if (activeNav === '城市分布') {
        // 城市分布：根据年份和方向变化
        const cityData = yearData.city_distribution;
        const cities = Object.keys(cityData);
        const values = Object.values(cityData);
        const maxVal = Math.max(...values);
        
        option = {
            tooltip: { trigger: 'item', formatter: '{b}: {c}人' },
            grid: { left: '5%', right: '5%', bottom: '10%', top: '15%', containLabel: true },
            xAxis: { type: 'category', data: cities, axisLine: { lineStyle: { color: '#1e3a8a' } } },
            yAxis: { type: 'value', name: '毕业生流向人数', splitLine: { lineStyle: { type: 'dashed' } } },
            series: [{
                type: 'scatter', data: values,
                symbolSize: val => Math.max((val / maxVal) * 60, 20),
                itemStyle: { color: '#3b82f6', shadowBlur: 10, shadowColor: 'rgba(59, 130, 246, 0.5)' }
            }]
        };
    } else if (activeNav === '平均薪资') {
        // 平均薪资：根据年份和方向变化
        const salaryData = yearData.salary_distribution;
        option = {
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            grid: { left: '5%', right: '5%', bottom: '10%', top: '15%', containLabel: true },
            xAxis: { type: 'category', data: Object.keys(salaryData), axisLabel: { interval: 0, rotate: 30 } },
            yAxis: { type: 'value', name: '人数', splitLine: { lineStyle: { type: 'dashed' } } },
            series: [{
                type: 'bar', barWidth: '50%', data: Object.values(salaryData),
                itemStyle: { color: '#2563eb', borderRadius: [8, 8, 0, 0] },
                label: { show: true, position: 'top' }
            }]
        };
    } else if (activeNav === '行业分布') {
        // 行业分布：根据年份和方向变化
        const industryData = yearData.industry_distribution;
        const pieData = Object.keys(industryData).map(k => ({ name: k, value: industryData[k] }));
        option = {
            tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
            series: [{
                type: 'pie', radius: ['40%', '70%'], center: ['50%', '50%'],
                itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
                data: pieData
            }]
        };
    } else if (activeNav === '热门岗位') {
        // 热门岗位：不随年份变化，使用独立数据源
        const hotPositions = window.hotPositionsData[roleKey];
        const wordCloudData = hotPositions.map((item, index) => ({ name: item.position, value: 100 - index * 5 }));
        option = {
            tooltip: { show: true },
            series: [{
                type: 'wordCloud', shape: 'circle', sizeRange: [20, 60], rotationRange: [-45, 45],
                textStyle: { color: () => ['#1d4ed8', '#2563eb', '#3b82f6', '#60a5fa'][Math.floor(Math.random() * 4)] },
                data: wordCloudData
            }]
        };
    }
    dashboardChart.setOption(option, true);
}

// Dashboard Sidebar Events
document.querySelectorAll('#role-sidebar button').forEach(btn => {
    btn.addEventListener('click', (e) => {
        document.querySelectorAll('#role-sidebar button').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        activeRole = e.target.dataset.role;
        renderDashboard();
    });
});
document.querySelectorAll('#year-sidebar button').forEach(btn => {
    btn.addEventListener('click', (e) => {
        document.querySelectorAll('#year-sidebar button').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        activeYear = e.target.dataset.year;
        renderDashboard();
    });
});

// Detail Logic
function renderDetail() {
    document.getElementById('detail-title').innerText = `${detailDirection}方向`;
    document.getElementById('detail-hero-img').src = `https://picsum.photos/seed/${detailDirection}/1920/600`;
    
    // Update Tabs
    const tabsContainer = document.getElementById('detail-tabs');
    tabsContainer.innerHTML = '';
    const tabs = ['介绍', '技术', '薪资'];
    tabs.forEach(tab => {
        const btn = document.createElement('button');
        btn.innerText = tab;
        if (tab === detailTab) btn.classList.add('active');
        btn.addEventListener('click', () => {
            detailTab = tab;
            renderDetail();
        });
        tabsContainer.appendChild(btn);
    });

    // Update Content
    document.querySelectorAll('.detail-pane').forEach(pane => pane.classList.remove('active'));
    document.getElementById('detail-content-title').innerText = `${detailDirection}方向介绍`;

    if (detailTab === '介绍') {
        document.getElementById('pane-intro').classList.add('active');
        // 更新介绍内容
        const introText = directionIntro[detailDirection] || '暂无介绍';
        document.getElementById('pane-intro').innerHTML = `<p>${introText}</p>`;
    } else if (detailTab === '技术') {
        document.getElementById('pane-tech').classList.add('active');
        document.getElementById('detail-content-title').innerText = `${detailDirection}方向技术能力分析`;
        setTimeout(renderTechChart, 100);
    } else if (detailTab === '薪资') {
        document.getElementById('pane-salary').classList.add('active');
        document.getElementById('detail-content-title').innerText = `${detailDirection}方向薪资趋势分析`;
        setTimeout(renderSalaryChart, 100);
    }
}

function renderTechChart() {
    if (!techChart) techChart = echarts.init(document.getElementById('detail-tech-chart'));
    
    // 根据不同方向设置不同的技术雷达图数据
    let radarData = [];
    if (detailDirection === '前端开发') {
        radarData = [
            { name: 'HTML/CSS', max: 100, value: 95 },
            { name: 'JavaScript', max: 100, value: 90 },
            { name: 'React/Vue', max: 100, value: 88 },
            { name: '工程化', max: 100, value: 80 },
            { name: '性能优化', max: 100, value: 75 },
            { name: '跨平台', max: 100, value: 70 }
        ];
    } else if (detailDirection === '后端开发') {
        radarData = [
            { name: 'Java/Go', max: 100, value: 92 },
            { name: '数据库', max: 100, value: 88 },
            { name: '高并发', max: 100, value: 85 },
            { name: '微服务', max: 100, value: 82 },
            { name: '系统设计', max: 100, value: 78 },
            { name: 'DevOps', max: 100, value: 70 }
        ];
    } else if (detailDirection === '人工智能') {
        radarData = [
            { name: '机器学习', max: 100, value: 95 },
            { name: '深度学习', max: 100, value: 92 },
            { name: '数学基础', max: 100, value: 90 },
            { name: 'CV/NLP', max: 100, value: 85 },
            { name: '大模型', max: 100, value: 88 },
            { name: '工程实现', max: 100, value: 75 }
        ];
    } else if (detailDirection === '数据分析') {
        radarData = [
            { name: 'SQL', max: 100, value: 90 },
            { name: 'Python', max: 100, value: 88 },
            { name: 'BI工具', max: 100, value: 85 },
            { name: '统计学', max: 100, value: 82 },
            { name: '可视化', max: 100, value: 80 },
            { name: '业务理解', max: 100, value: 85 }
        ];
    } else if (detailDirection === '算法工程') {
        radarData = [
            { name: '算法设计', max: 100, value: 92 },
            { name: '工程优化', max: 100, value: 90 },
            { name: '高性能编程', max: 100, value: 88 },
            { name: '推荐系统', max: 100, value: 85 },
            { name: '搜索算法', max: 100, value: 82 },
            { name: '系统设计', max: 100, value: 80 }
        ];
    }
    
    techChart.setOption({
        tooltip: { trigger: 'item' },
        radar: { indicator: radarData.map(i => ({ name: i.name, max: i.max })) },
        series: [{ type: 'radar', data: [{ value: radarData.map(i => i.value), name: '技术能力要求', itemStyle: { color: '#2563eb' }, areaStyle: { color: 'rgba(37, 99, 235, 0.4)' } }] }]
    });
}

function renderSalaryChart() {
    if (!salaryChart) salaryChart = echarts.init(document.getElementById('detail-salary-chart'));
    
    // 根据不同方向设置不同的薪资数据
    let salaryData = [];
    let growthData = [];
    
    if (detailDirection === '前端开发') {
        salaryData = [12.5, 14.8, 17.2, 19.5, 22.0];
        growthData = [8.5, 18.4, 16.2, 13.4, 12.8];
    } else if (detailDirection === '后端开发') {
        salaryData = [14.2, 16.8, 19.5, 22.8, 26.5];
        growthData = [9.2, 18.3, 16.1, 16.9, 16.2];
    } else if (detailDirection === '人工智能') {
        salaryData = [18.5, 22.5, 27.0, 32.5, 38.0];
        growthData = [12.5, 21.6, 20.0, 20.4, 16.9];
    } else if (detailDirection === '数据分析') {
        salaryData = [13.0, 15.2, 17.8, 20.5, 23.5];
        growthData = [8.0, 16.9, 17.1, 15.2, 14.6];
    } else if (detailDirection === '算法工程') {
        salaryData = [16.5, 20.0, 24.5, 29.5, 35.0];
        growthData = [10.5, 21.2, 22.5, 20.4, 18.6];
    }
    
    salaryChart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
        legend: { data: ['平均年薪 (万)', '同比增长率 (%)'] },
        xAxis: [{ type: 'category', data: ['2020', '2021', '2022', '2023', '2024'] }],
        yAxis: [
            { type: 'value', name: '平均年薪 (万)', min: 0, max: 45 },
            { type: 'value', name: '增长率 (%)', min: 0, max: 30 }
        ],
        series: [
            { name: '平均年薪 (万)', type: 'bar', data: salaryData, itemStyle: { color: '#3b82f6' } },
            { name: '同比增长率 (%)', type: 'line', yAxisIndex: 1, data: growthData, itemStyle: { color: '#f59e0b' }, lineStyle: { width: 4 } }
        ]
    });
}

document.getElementById('detail-year-select').addEventListener('change', (e) => {
    detailYear = e.target.value;
    renderDetail();
});

window.addEventListener('resize', () => {
    if (dashboardChart) dashboardChart.resize();
    if (techChart) techChart.resize();
    if (salaryChart) salaryChart.resize();
});

// Initialize
switchView('home');
