# 最终入选候选的逐条偏差索引

保留原 GOLD；以下不是重新标注。每条链接指向完整冻结证据、GOLD 理由和正式匹配 trace。未为追分修改已冻结候选。

## INTC · attempt-02-overfit

补充硅自旋计算、处理器接口、制造设备及实际计算部署信息。剩余 FP 包括把“Open Line Systems”“building entrances”解释为设施动作。`intel_manufacturing_fact` 的一个分支在 Intel 后缺少独立词边界，实际命中了 Artificial Intelligence；同一文章另有封装组命中。该词面缺陷是本轮明确遗留，不能因总分过线忽略。FN 仍有正文中的具体项目、EDA 及互连事实。

### development · 8 个偏差

- **F35c51468711dfd-INTC · FP**：STMicroelectronics reveals advanced microcontrollers for real-time power control in AI datacenters and robotics
  GOLD：STM发布电源和电机控制MCU，未涉及Intel核心PC、服务器或边缘计算处理器市场。
  匹配：facility_opening: build more efficient, intelligent, and secure technology for datacenters
- **F4b5ad2edf6e2dd-INTC · FP**：Ciena, Nokia, and Cisco Named Leaders in ABI Research’s Open Line Systems for Data Center Interconnect Scale-Across Ranking
  GOLD：光线路系统竞争和网络架构并无具体处理器采购或计算设施建设事实。
  匹配：facility_subject_event: Data Center / Open；facility_opening: Open Line Systems for Data Center
- **F4d58332ac363c2-INTC · FN**：Looking Past the Chips: Tuttle Capital Launches the AI Bottleneck ETF (DLAY)
  GOLD：ETF投资论点明确指出先进封装和逻辑代工产能集中、需求超过供给，与Intel代工封装业务相邻。
  匹配：没有放行组命中
- **F8e0e659f597556-INTC · FN**：Compass Datacenters Makes One-Time $5 Million Gift to Meridian's Public Schools
  GOLD：Meridian数据中心园区正在建设并调试首栋楼，构成具体计算设施建设。
  匹配：没有放行组命中
- **Fafa888b0027c1f-INTC · FN**：AIB Advances CLT1 Delivery With Funded Prepayment and Executed Equipment Orders
  GOLD：CLT1 数据中心已签 50 MW IT 容量协议并采购电力设备，属于具体计算设施建设。
  匹配：没有放行组命中
- **Fc2f5434533ad22-INTC · FP**：Millennium Adds R&M Data Center Connectivity to Its Integrated Supply Chain Platform
  GOLD：合作销售数据中心光纤和铜连接产品，没有具体算力设施部署或处理器市场事实。
  匹配：facility_opening: building entrances, campuses and data center
- **Ff7a692424f18ce-INTC · FN**：Fabric.AI Files U.S. Patent Application to Put Surplus MicroLED Channels to Work for Fault Tolerance and Faster GPU Synchronization
  GOLD：新型数据中心光互连用于 GPU 同步，与英特尔相关互连及数据中心技术相邻。
  匹配：没有放行组命中
- **Ff891e2a6a6aef2-INTC · FP**：Crusoe Announces Wyoming Data Center Campus Update
  GOLD：数据中心项目收购及供电规划未涉及英特尔处理器。
  匹配：facility_subject_event: Data Center / Campus；facility_capacity_progress: contracted and over 40 GW / data center；facility_opening: building AI-optimized data centers

完整证据：[development/badcases.jsonl](INTC/regex/attempt-02-overfit/development/badcases.jsonl)。

### validation · 7 个偏差

- **F0f0305a3d35c51-INTC · FN**：What Happens When a Data Center in Space Breaks Down?
  GOLD：文中描述SpaceX申报百万颗AI数据中心卫星及具体设计，涉及早期大型计算部署规划。
  匹配：没有放行组命中
- **F26b34cb657f07a-INTC · FP**：India Construction Consulting Market Size, Share, Industry Trends, and Growth Forecasts, 2026-2031 | Infrastructure, Data Centers, and BIM Fuel 7.8% CAGR
  GOLD：报告涉及半导体设施建筑咨询，未指出英特尔项目或芯片生产事实。
  匹配：facility_subject_event: Data Centers / Construction
- **F2c575d6e64a1b8-INTC · FP**：TGI Solar Power Group and TGI AMIRON Announce Preliminary Design Completion for Planned Aktau AI Data Center Campus
  GOLD：数据中心概念设计未指定英特尔处理器或芯片供应。
  匹配：facility_subject_event: Data Center / Completion；facility_capacity_progress: campus electrical design targets an initial 200 MW, with a design pathway to 500 MW / Data Center
- **F60671a67e9fb42-INTC · FN**：Semiconductor IC Design EDA Tools Market - Global Forecast 2026-2032 | AI Automation, Chiplets, and Advanced Packaging Drive 8.57% CAGR Toward USD 17.21 Billion
  GOLD：芯片设计自动化、先进节点及芯粒封装直接关联英特尔芯片设计与代工生态。
  匹配：没有放行组命中
- **Fb2dbb8e61149e4-INTC · FN**：AI Data Center Suppliers Report Growth as October Inflation Data Reaches Wall Street
  GOLD：Onto 的芯片检测及先进封装设备增长涉及 Intel 半导体制造供应链。
  匹配：没有放行组命中
- **Fbd9162c251f043-INTC · FP**：Global Semiconductor Design, Fabrication & OSAT Market Forecast 2026-2032 | Boost ROI Through Multi-Source Supply, Advanced Packaging Partnerships, and Balanced Node Investments
  GOLD：Intel 仅列入公司名单，正文是通用半导体行业概况。
  匹配：intel_manufacturing_fact: Intelligence Across the Semiconductor；logic_packaging_fact: Advanced Packaging / wafer
- **Fd18a526adb24e1-INTC · FN**：Auddia Highlights Accelerating Interest in Patented LT350 Distributed AI Datacenter Technology Following Election as Opposition to Traditional Data Centers Intensifies
  GOLD：LT350 开发模块化 GPU 推理基础设施并报告客户兴趣，属于具体计算产品进展。
  匹配：没有放行组命中

完整证据：[validation/badcases.jsonl](INTC/regex/attempt-02-overfit/validation/badcases.jsonl)。

## MU · attempt-03-overfit

补充存储成本/价格、外部存储替代、制造检测和大规模客户设施事实，验证召回明显改善。仍有同篇电池储能容量与数据中心背景凑合、器件说明中的普通建设动词误召；不能将这一轮的高召回解释为全市场高召回。

### development · 3 个偏差

- **F35c51468711dfd-MU · FP**：STMicroelectronics reveals advanced microcontrollers for real-time power control in AI datacenters and robotics
  GOLD：新MCU用于电源控制，片上Flash容量是产品参数，未披露独立存储器市场变化。
  匹配：facility_opening: build more efficient, intelligent, and secure technology for datacenters
- **F8d3042ddd703ef-MU · FP**：GridPoint targets 2 GW of battery storage across 20,000+ commercial sites as U.S. power demand surges
  GOLD：储能指电池储电，非半导体存储器；AI算力只作电力需求背景。
  匹配：facility_capacity_progress: buildings, today announced plans to expand its platform with on-site battery storage, targeting 2 gigawatts / data centers
- **Ffc30b5690fcbbf-MU · FP**：WISeQey, SEALSQ, SEALCOIN and Eco Wave Power Sign Three-Year MoU and Demonstrate Trusted Energy Transactions for AI Data Centers
  GOLD：项目采用安全半导体和能源交易技术，未涉及存储器产品或供应。
  匹配：facility_capacity_progress: planned in Portugal, Taiwan, and India, representing a project pipeline of 404.7 MW / data centers

完整证据：[development/badcases.jsonl](MU/regex/attempt-03-overfit/development/badcases.jsonl)。

### validation · 1 个偏差

- **F65e9877dd08571-MU · FP**：Global Thermal Energy Storage Market Size, Share, Trends, Industry Analysis, and Forecast, 2026-2036 | Market to Reach USD 22.97 Billion as Industrial Heat, Seasonal Storage, and Data Centers Scale
  GOLD：数据中心制冷和余热仅为热储能市场应用预测，未涉及存储芯片或具体计算设施。
  匹配：facility_opening: building HVAC, and data center

完整证据：[validation/badcases.jsonl](MU/regex/attempt-03-overfit/validation/badcases.jsonl)。

## BE · attempt-02-overfit

删去移动燃料电池、便携电源及过远核燃料题材的泛匹配，保留固定供电替代、储能、负荷和电网信息。验证余下5个 FP：公司介绍、连接器终端用途、热储能背景，以及2个业务边界争议；漏掉实际楼宇储能项目。原 GOLD 下精度不足。

### development · 4 个偏差

- **F35566d0548c0b4-BE · FN**：North America Medium Voltage Cable and Accessories Market Size, Share, Trends, and Forecast 2021 - 2031 | Grid Modernization and Renewables Drive Growth to US$7.97 Billion
  GOLD：报告披露北美电网韧性计划和供电线缆需求，涉及电力网络及分布式供电接入环境。
  匹配：没有放行组命中
- **F8e0e659f597556-BE · FN**：Compass Datacenters Makes One-Time $5 Million Gift to Meridian's Public Schools
  GOLD：Compass披露Meridian数据中心建设、首栋调试及所需电网投资，与大型负荷供电相邻。
  匹配：没有放行组命中
- **Ff9b7e66739cd35-BE · FP**：ONCOR REPORTS THIRD QUARTER 2026 RESULTS
  GOLD：电网互联队列虽含数据中心负荷，但未涉及燃料电池或现场发电项目。
  匹配：grid_commitment_fact: transmission and distribution capital investments；facility_capacity_progress: energized load, and 21 GW / data centers
- **Ffcec5c9f7ef925-BE · FP**：Pure DC Unveils ~$2Billion Financing Framework to Scale Its Differentiated Pan-European & Middle East Cloud & AI Infrastructure Platform
  GOLD：数据中心融资和电力接入策略未说明燃料电池等具体供电技术。
  匹配：facility_opening: Build Project of the Year at the Data Centre

完整证据：[development/badcases.jsonl](BE/regex/attempt-02-overfit/development/badcases.jsonl)。

### validation · 6 个偏差

- **F22b4a325410cd4-BE · FP**：Eguana Announces Completion of Second and Final Tranche and Closing of Previously Announced Convertible Debenture Financing
  GOLD：融资公告中燃料电池仅在公司业务介绍提及。
  匹配：stationary_supply_fact: fuel cell
- **F2c575d6e64a1b8-BE · FP**：TGI Solar Power Group and TGI AMIRON Announce Preliminary Design Completion for Planned Aktau AI Data Center Campus
  GOLD：规划使用燃气发电和储能，未涉及燃料电池。
  匹配：facility_subject_event: Data Center / Completion；facility_capacity_progress: campus electrical design targets an initial 200 MW, with a design pathway to 500 MW / Data Center
- **F65e9877dd08571-BE · FP**：Global Thermal Energy Storage Market Size, Share, Trends, Industry Analysis, and Forecast, 2026-2036 | Market to Reach USD 22.97 Billion as Industrial Heat, Seasonal Storage, and Data Centers Scale
  GOLD：热储能市场侧重工业热及区域供冷，与燃料电池或电化学电网储能无具体关联。
  匹配：large_load_outlook: data center electricity consumption；facility_opening: building HVAC, and data center
- **Fbc9ab132bceba9-BE · FP**：Canada Elbow Connectors Market Size, Trends, and Forecast 2025-2031 | Grid Modernization and Renewable Energy Investment Accelerate MV HV Switchgear Demand
  GOLD：加拿大电力连接器市场仅泛述分布式发电，并无燃料电池或具体供电项目。
  匹配：stationary_supply_fact: distributed generation
- **Fd97f09b71d1a6b-BE · FP**：Nuclear Power Market - Global Forecast 2026-2032 | Market to Reach USD 75.98 Billion at 7.89% CAGR as SMRs, Life Extensions, and Fuel Security Drive Growth
  GOLD：核电市场预测与燃料电池无具体联系。
  匹配：power_market_subject: Nuclear Power Market
- **Fe67ee33c1dfa29-BE · FN**：Viridi, Good Carbon and National Grid Named 2026 Fortnightly Top Innovator of the Year for Breakthrough Building-to-Grid Project
  GOLD：布法罗住宅楼已部署 3.75 MWh 储能并参与电网需求响应，属于具体固定储能项目。
  匹配：没有放行组命中

完整证据：[validation/badcases.jsonl](BE/regex/attempt-02-overfit/validation/badcases.jsonl)。

## COHR · attempt-02

第二轮修复光纤连接、专用激光、单位和连字符，开发集100%/100%。验证的背景施工、相关报告推荐区及泛数据中心主题仍导致5个 FP；具体算力项目/融资的2个 FN未覆盖。额外版精度提高至70%，但召回降至70%，四项均分低于第二轮，按 SOP 保留第二轮为数值最佳；不建议作为生产候选。

### development · 0 个偏差


完整证据：[development/badcases.jsonl](COHR/regex/attempt-02/development/badcases.jsonl)。

### validation · 7 个偏差

- **F0672cdfcd7da7d-COHR · FP**：Vicat - Q3 2026 Trading update
  GOLD：水泥业务提及一般数据中心建设需求，但没有具体供电项目或光通信设备事实。
  匹配：facility_opening: construction of data centers
- **F26b34cb657f07a-COHR · FP**：India Construction Consulting Market Size, Share, Industry Trends, and Growth Forecasts, 2026-2031 | Infrastructure, Data Centers, and BIM Fuel 7.8% CAGR
  GOLD：印度建筑咨询报告未涉及光通信器件具体需求。
  匹配：facility_opening: Construction Consulting Market Size, Share, Industry Trends, and Growth Forecasts, 2026-2031 | Infrastructure, Data Centers
- **F520f8f6ccd01bf-COHR · FN**：AI Inference Chips Market Size, Share, and Industry Analysis - Global Forecast to 2036 | Market to Reach USD 589.5 Billion as Custom ASICs, Agentic AI, and Edge Deployment Drive 16.5% CAGR
  GOLD：正文披露 Stargate UAE 具体 200 MW 数据中心上线计划，与光通信基础设施需求相邻。
  匹配：没有放行组命中
- **F8179385c35b534-COHR · FP**：Global AI Data Center Market to Reach USD 84.52 Billion by 2032, Growing at a CAGR of 25.8% - Report by Maximize Market Research
  GOLD：报告泛提数据中心网络，光收发器只列在相关报告标题。
  匹配：optical_fact: Optical Transceiver；compute_facilities_market: Data Center Market；capacity_project: Data Center Infrastructure Market Analysis & Growth Consulting** Drax, Brookfield and Radiant Partner on a Gigawatt-Scale
- **Fd0f61de5ff7d09-COHR · FP**：Wire and Cable Market in India 2026–2032: USD 39.47 Billion Opportunity Driven by Infrastructure Expansion and Renewable Energy Investments, Reports Mordor Intelligence
  GOLD：印度通信光纤线缆市场展望未涉及 COHR 光器件或具体光通信产品。
  匹配：facility_opening: Construction activity, telecom fiberization, data center
- **Fd67ca2428cf5a0-COHR · FN**：13 Top Press Releases from October
  GOLD：汇编独立条目披露 Broadcom 联合融资 630 亿美元建设 AI 计算容量，邻近数据中心光互联需求。
  匹配：没有放行组命中
- **Fef61acfdc72963-COHR · FP**：Edgevana Signs $700 Million in AI Infrastructure Agreements as Committed Demand Reaches $7 Billion
  GOLD：AI 算力协议只泛提高速网络，没有光收发器产品或采购事实。
  匹配：compute_deployment: deploy NVIDIA B300

完整证据：[validation/badcases.jsonl](COHR/regex/attempt-02/validation/badcases.jsonl)。

## NBIS · attempt-03-overfit

额外版移除泛 AI 基础设施标题和组件广告，验证20%的原负例误召降至0/32。对应的5个 FN主要是供电试验/行业论坛、同业市场整合、项目统计和电源协议；把容量工程条件收得过严。开发仍有背景容量和连接产品误放。

### development · 5 个偏差

- **F060d873c22a0fd-NBIS · FN**：Global Data Center Power, Cooling and Energy Storage Market 2027-2037 | AI Demand and High-Density Racks Drive Grid-to-Chip Infrastructure Investment
  GOLD：报告讨论AI数据中心机架功率、并网限制与现场供电冷却，涉及AI云设施部署条件。
  匹配：没有放行组命中
- **F5c7e86e65d816a-NBIS · FP**：Global Fusion Energy Market Size, Share, Trends, and Industry Analysis - Forecast 2026-2036 | Market to Reach USD 30.94 Billion as Private Funding, AI Power Demand, and Commercial Offtake Accelerate
  GOLD：报告讨论聚变发电项目和购电协议，未涉及具体云算力设施建设或租赁。
  匹配：facility_capacity_progress: contracted 200 MW / data centers
- **Fafa888b0027c1f-NBIS · FN**：AIB Advances CLT1 Delivery With Funded Prepayment and Executed Equipment Orders
  GOLD：CLT1 的 50 MW AI/HPC 数据中心项目已签客户协议并推进采购，涉及云算力设施供给。
  匹配：没有放行组命中
- **Fba9c7c247e2da6-NBIS · FN**：Global Data Center Electrical Equipment Market Size, Share, Trends, and Forecast, 2026-2036 | AI and Hyperscale Demand to Drive 11.8% CAGR, Reaching USD 98.6 Billion by 2036
  GOLD：报告描述吉瓦级 AI 数据中心建设及供电限制，涉及云计算设施容量和交付条件。
  匹配：没有放行组命中
- **Fc2f5434533ad22-NBIS · FP**：Millennium Adds R&M Data Center Connectivity to Its Integrated Supply Chain Platform
  GOLD：数据中心连接产品分销合作未披露云算力容量或具体建设项目。
  匹配：facility_opening: building entrances, campuses and data center

完整证据：[development/badcases.jsonl](NBIS/regex/attempt-03-overfit/development/badcases.jsonl)。

### validation · 5 个偏差

- **F03116b771657c9-NBIS · FN**：Hundreds of Industry Leaders to Convene in Austin, Texas for the NatGas To Power Forum, Exploring Solutions to AI Data Center Energy Demand
  GOLD：论坛具体讨论数据中心新增电力需求、供电瓶颈和选址，关联 AI 云基础设施。
  匹配：没有放行组命中
- **F4106c5368328f8-NBIS · FN**：From Hype to Commercial Reality: ABI Research Reveals 60 Technology Predictions for 2027, Including AI Data Center Consolidation and Robotics Growth
  GOLD：报告预测AI云市场整合及有电力保障的云厂商竞争，涉及NBIS同业市场。
  匹配：没有放行组命中
- **Fc59b03101c7a86-NBIS · FN**：FuelCell Energy to Host National Laboratory-Supported AI Data Center Power Testbed in Connecticut Manufacturing Facility
  GOLD：针对 AI 数据中心电力波动的具体供电测试项目与 Nebius 算力基础设施相邻。
  匹配：没有放行组命中
- **Fd73d9d3deb215c-NBIS · FN**：FCEL FINAL DEADLINE ALERT: Hagens Berman Encourages FuelCell Energy Investors with Losses to Contact Firm Before Nov. 10th Deadline in Securities Class Action
  GOLD：诉讼正文披露 FuelCell 与 Fit Energy 的最高 380 MW 数据中心电源协议及成本损失。
  匹配：没有放行组命中
- **Fff5186cffd8bbd-NBIS · FN**：2026 Data Center Announcements Pass All of 2025 as Local Restrictions Spread to 42 States
  GOLD：报告统计美国在开发及新公告数据中心项目，涉及实际云算力设施供给。
  匹配：没有放行组命中

完整证据：[validation/badcases.jsonl](NBIS/regex/attempt-03-overfit/validation/badcases.jsonl)。

## CEG · attempt-02-overfit

补充发电市场、核能项目及具体负荷事实，去除泛电网背景。验证无 FP，但4个 FN包括超导输电、储能设施投运、独立太阳能储能项目和电厂维护产品；行业范围/表达仍不完整。

### development · 2 个偏差

- **F01194ec27fdd4a-CEG · FN**：Century Aluminum Company Reports Third Quarter 2026 Results
  GOLD：财报披露极端高温带来不利电价，并提及原厂址在建数据中心及其投运风险，与发电及用电需求相邻。
  匹配：没有放行组命中
- **F35566d0548c0b4-CEG · FN**：North America Medium Voltage Cable and Accessories Market Size, Share, Trends, and Forecast 2021 - 2031 | Grid Modernization and Renewables Drive Growth to US$7.97 Billion
  GOLD：报告讨论北美输配电线缆市场及电网韧性项目资金，与电力输配体系直接相邻。
  匹配：没有放行组命中

完整证据：[development/badcases.jsonl](CEG/regex/attempt-02-overfit/development/badcases.jsonl)。

### validation · 4 个偏差

- **F43f94522bd1ea3-CEG · FN**：Global Superconducting Power Cables Market Size, Share, Trends, and Forecast, 2026-2033 | Grid Modernization and Renewable Integration Drive 8.8% CAGR to USD 5.5 Billion
  GOLD：报告分析电网拥塞和超导电缆输电升级，涉及电力传输和发电接入条件。
  匹配：没有放行组命中
- **F861b60559af84d-CEG · FN**：Alectra Energy Solutions and Convergent Energy and Power Complete Three Battery Storage Facilities Supporting Ontario’s Electricity Grid
  GOLD：安大略三座储能设施投运，将低谷核电转移至高峰，涉及核电与电网调度。
  匹配：没有放行组命中
- **F98513d341e7225-CEG · FN**：GCC Wires and Cables Market Size, Share, Trends, and Company Analysis, 2026-2034 | Smart Grids, Renewable Energy, and Infrastructure Drive 10.04% CAGR to US$12.11 Billion
  GOLD：海湾电缆市场报告还披露阿布扎比一吉瓦连续供电的太阳能储能实际项目。
  匹配：没有放行组命中
- **Fe66cc80a582604-CEG · FN**：Visionaize Launches V-BTHERM an AI Driven Boiler Thermal Stress Prediction for Flexible Power Generation
  GOLD：新推出的火电锅炉热应力预测产品面向电厂运行和维护，邻近发电运营。
  匹配：没有放行组命中

完整证据：[validation/badcases.jsonl](CEG/regex/attempt-02-overfit/validation/badcases.jsonl)。

## RKLB · attempt-02-overfit

补齐月球经济、空间供电、卫星制造与连接生态，验证27个正例全部保留。余下误召来自导航用途、卫星业务模板及一个卫星连接业务边界争议，不能单凭 satellite/constellation 共现当成独立经营事件。

### development · 1 个偏差

- **Ffc30b5690fcbbf-RKLB · FP**：WISeQey, SEALSQ, SEALCOIN and Eco Wave Power Sign Three-Year MoU and Demonstrate Trusted Energy Transactions for AI Data Centers
  GOLD：卫星业务仅在合作方公司简介中列出，主体为能源交易示范。
  匹配：space_contract_fact: satellite launches

完整证据：[development/badcases.jsonl](RKLB/regex/attempt-02-overfit/development/badcases.jsonl)。

### validation · 2 个偏差

- **F8146e02424480b-RKLB · FP**：Global Unmanned Aerial Vehicle (UAV) Navigation Systems Market Report 2026 | Capitalize on 18.3% CAGR as Honeywell, BAE Systems, Thales, Safran and Shield AI Vie for Share
  GOLD：无人机导航和卫星定位作为技术手段，未涉及卫星制造或发射业务。
  匹配：orbital_service_fact: satellite systems / growth
- **Fb0f3ecac5ceb35-RKLB · FP**：Gogo Announces Third Quarter Results
  GOLD：机载卫星连接服务使用现有网络，没有具体卫星制造或发射计划。
  匹配：space_contract_fact: satellite constellations；orbital_service_fact: satellite network / growth

完整证据：[validation/badcases.jsonl](RKLB/regex/attempt-02-overfit/validation/badcases.jsonl)。

## SNOW · attempt-02-overfit

补齐实时数据库、工业数据平台、跨系统数据治理及平台实际采用。验证13/14个正例保留，剩余垂直医疗数据词误召、公司正式财报通知的 GOLD 争议和产品信息管理漏判；通用数据层与垂直应用的边界仍较难用关键词表达。

### development · 3 个偏差

- **F82659e97d4263b-SNOW · FP**：Global Healthcare Knowledge Management Market Size, Share, Trends, and Forecast 2026-2036 | AI and Cloud Adoption Fuel 12.0% CAGR and a USD 9.68 Billion Opportunity
  GOLD：报告聚焦医疗知识库、临床决策与文档检索等垂直应用，未具体讨论企业数据平台市场。
  匹配：enterprise_data_architecture: Data Silos
- **F9fbac39d38c271-SNOW · FN**：Healthcare Cloud Computing Market to Reach US$ 71.30 Billion by 2034 at 12.16% CAGR, Driven by Growing Adoption of Digital Health Platforms and Healthcare Data Management Solutions | Exclusive Report by The Insight Partners
  GOLD：医疗云报告分析跨机构数据管理、互操作及医疗分析平台需求增长，与企业数据基础设施需求相邻。
  匹配：没有放行组命中
- **Fdf81a769d384a1-SNOW · FP**：Revenue Cycle Management Market to worth USD 132.06 Billion by 2031, at a CAGR of 12.4% – MarketsandMarkets™
  GOLD：报告涉及医疗账单及理赔管理垂直应用，不是通用企业数据平台市场。
  匹配：enterprise_data_architecture: data silos

完整证据：[development/badcases.jsonl](SNOW/regex/attempt-02-overfit/development/badcases.jsonl)。

### validation · 3 个偏差

- **F44f57583483071-SNOW · FN**：Inriver Achieves SAP Spotlight+ Partner Tier, as PIM becomes central to success in AI-Driven Commerce
  GOLD：Inriver连接ERP、工程与供应商产品数据并进行治理分发，涉及企业数据集成层。
  匹配：没有放行组命中
- **F7693f15c2c802a-SNOW · FP**：Molecular Tumor Board Solutions Market Size, Share, Trends, and Global Forecast, 2026-2036 | AI-Driven Precision Oncology and Cloud Adoption Fuel 13.2% CAGR to $6.63 Billion
  GOLD：肿瘤诊疗专用软件报告不涉及通用企业数据平台具体业务。
  匹配：enterprise_data_architecture: Data Interoperability
- **F7be20ec30037f3-SNOW · FP**：MongoDB, Inc. Announces Date of Third Quarter Fiscal 2027 Earnings Call
  GOLD：MongoDB 仅预告财报电话会，业务描述在公司简介。
  匹配：data_platform_subject: MongoDB

完整证据：[validation/badcases.jsonl](SNOW/regex/attempt-02-overfit/validation/badcases.jsonl)。

## ONDS · attempt-02-overfit

补齐反无人机、无人地面系统、专网、无人机组件与自主飞行器。验证由21/32提升至30/32，仍误收公司简介/历史用途事实，漏掉威胁检测合作及无人系统经营更新；开发召回由87.5%降至81.25%，属修正代价。

### development · 8 个偏差

- **F284211ae793633-ONDS · FN**：J-Star Holding Co., Ltd. Provides Update on Capital Structure Authorization and Strategic Execution Timeline
  GOLD：J-Star更新资本计划时称正扩展碳纤维无人机机体制造能力，涉及无人机机体供应。
  匹配：没有放行组命中
- **F31d79208017ae7-ONDS · FN**：Feralx Systems Inc. Appoints Dr. Trevor Devine Head of Aviation and Aerospace
  GOLD：FeralX任命航空及无人系统业务负责人并规划无人机制造，同时披露加拿大无人机采购预算。
  匹配：没有放行组命中
- **F5ae75dc7694b30-ONDS · FN**：Global Armed Light Reconnaissance Aircraft Market Report 2026-2030 | Capitalize on Autonomous ISR and Multi-Mission Demand with Boeing, Lockheed Martin, L3Harris, and Embraer
  GOLD：报告明确涵盖无人及可选有人侦察飞机，并讨论自主侦察需求增长。
  匹配：没有放行组命中
- **F688f4fd2b9e9d7-ONDS · FP**：Kratos and Seagate Space Partner to Advance Mobile Offshore Launch Capabilities for National Security Missions
  GOLD：协议为海上火箭发射和靶标任务平台，无人机只出现在Kratos公司业务介绍。
  匹配：counter_unmanned_systems: counter UAS
- **F689faf89ec171a-ONDS · FN**：Saab receives order for advanced live training systems from Denmark
  GOLD：丹麦采购实兵训练系统，包括无人机训练能力，涉及无人机相关训练配套采购。
  匹配：没有放行组命中
- **Fb1853465bde6e4-ONDS · FP**：Pantera Silver Commences Induced Polarization Survey at La Riviera Target, Rakanco Silver Project, Bolivia
  GOLD：银矿勘探启动地面电法测量，过去无人机磁测仅为数据来源。
  匹配：unmanned_business_fact: drone magnetic survey into the subsurface Test
- **Fb9d725e3d09d2e-ONDS · FN**：Physical AI and Manufacturing Company Nemi to Go Public at a $400 Million Pre-Money Equity Value Through Business Combination with BHAV Acquisition Corp
  GOLD：制造商披露为防务客户交付无人机动力系统，涉及无人机关键组件设计与制造。
  匹配：没有放行组命中
- **Fdd0d49dd727c98-ONDS · FN**：Machina Selected by U.S. Department of War to Deploy Expeditionary Robotic Factories for LUCAS Manufacturing
  GOLD：Machina获选开发无人机机体并部署两座机动制造工厂，属于无人系统采购制造。
  匹配：没有放行组命中

完整证据：[development/badcases.jsonl](ONDS/regex/attempt-02-overfit/development/badcases.jsonl)。

### validation · 4 个偏差

- **F007936b5610f82-ONDS · FP**：Gaxos Launches ArtGen Muse AI Video Model as Company Scales
  GOLD：视频生成产品与无人机无关，反无人机投资仅在公司简介。
  匹配：counter_unmanned_systems: counter-UAS
- **F82c230250b4027-ONDS · FP**：Factorial Announces Third Quarter 2026 Results, Begins Generating Commercial Revenue and Achieves 450 Wh/kg Energy Density Target for Aerospace Cells Ahead of Plan
  GOLD：无人机续航仅在电池公司介绍中提及。
  匹配：unmanned_business_fact: drone to a world record for flight
- **F9bacdd7c44d504-ONDS · FN**：Tessera Subsidiary Zorronet Expands D4P Group Collaboration into AI-Powered Critical Infrastructure and Homeland Security Markets Across the EU
  GOLD：Zorronet 扩展无人机威胁检测及反制合作，与 Ondas 无人机防御业务相邻。
  匹配：没有放行组命中
- **Fbb0e42066fc421-ONDS · FN**：Kratos Reports Third Quarter 2026 Financial Results
  GOLD：Kratos 无人机业务营收、订单及 Valkyrie 生产计划涉及 Ondas 相邻竞争业务。
  匹配：没有放行组命中

完整证据：[validation/badcases.jsonl](ONDS/regex/attempt-02-overfit/validation/badcases.jsonl)。

