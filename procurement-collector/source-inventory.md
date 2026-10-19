# 来源盘点与接入记录（2026-10-03）

黄总平台只读快照含 24 个来源和巡检规则，详见 [platform-sources.md](platform-sources.md)。本文件记录首批 5 个甲方站点的本机实测结果；账号凭据与会话未写入项目。

| 来源 | 类别/优先级 | 公开入口及典型详情 | 页面与访问方式 | 当前状态 |
| --- | --- | --- | --- | --- |
| 鞍钢智慧招投标 | 公告/P1，每日 | [列表](https://bid.ansteel.cn/zbjjxx/bidding_purchase.html)、[破碎机公告](https://bid.ansteel.cn/zbjjxx/001001/20260914/f42eb16c-bf67-47b9-ae52-129fc524d654.html) | JS 列表调用公开 POST 搜索接口；详情 HTML，附件有 PDF；投标需登录 | 已接入 |
| 华润守正 | 公告/P0，每日 | 快照旧域名 `shouzheng.crc.com.hk`；[现站](https://szecp.com.cn/) | 旧域名本机失效；现站网页和公开接口会触发 EdgeOne 安全验证；投标需登录 | 禁用，覆盖缺口 |
| 五矿鑫益采 | 公告/P1，每周 | `xinyicm.minmetals.com.cn` | 本机 HTTPS 连接失败，HTTP 502，DNS 未解析；页面与详情形式待核实 | 禁用，覆盖缺口 |
| 河钢云供应链 | 公告/P1，每周 | `hebiig.com` | 本机 HTTPS 连接失败，HTTP 502，DNS 未解析；页面与详情形式待核实 | 禁用，覆盖缺口 |
| 酒钢公共交易平台 | 公告/P1，每周 | [列表](https://eps.jiugangbid.com/jyxx/001002/001002002/jyxx_list.html)、[破碎机采购公告](https://eps.jiugangbid.com/jyxx/001002/001002002/20260828/b09b623f-eb6c-4878-a920-9cfb3e4880be.html?gonggaoguid=8b8e1713-421c-4d64-918c-629e0395cfc3) | JS 列表调用公开 POST 搜索接口；详情 HTML；部分采购文件下载需验证码 | 已接入 |

鞍钢、酒钢的搜索接口和详情均无需登录即可读取。采集器仅获取公开公告，不执行投标或下载受验证码保护的附件。华润现域名在诊断路由下可返回内容，但普通请求不稳定，故不计入自动采集覆盖。五矿、河钢的连接失败不等于没有公告。

## 补充来源

| 来源 | 内容 | 状态 |
| --- | --- | --- |
| [中国煤科物资采购](https://cg.ccteg.cn/cms/channel/ywgg1hw/index.htm)及[工程采购](https://cg.ccteg.cn/cms/channel/ywgg1gc/index.htm) | 矿业相关招采，静态 HTML 列表与详情 | 已接入 |
| [酒钢集团新闻](https://www.jiugang.com/news/article_list) | 企业新闻，正文关键词筛选矿业相关文章；[相关样本](https://www.jiugang.com/news/article/3360) | 已接入；是否属于正式目标企业名单待确认 |
| [自然资源部采矿权](https://ky.mnr.gov.cn/kyqcrgg/ckq/)及[探矿权](https://ky.mnr.gov.cn/kyqcrgg/tkq/) | 行业动态事实，不代表设备采购 | 已接入 |
| [中国政府采购网](https://www.ccgp.gov.cn/cggg/dfgg/gkzb/index.htm) | 地方公开招标 | 本机 403，禁用 |
| [全国公共资源交易平台](https://www.ggzy.gov.cn/deal/dealList.html?DEAL_CLASSIFY=04&DEAL_STAGE=0401) | 矿业权公告 | 本机代理连接失败，禁用 |

## 待补业务资料

- 现有招采日报样本，用于与系统日报逐条核对漏采。
- 正式目标矿业企业法定名称及新闻入口，用于扩展第二批企业新闻。
- 重点设备品牌、型号、料号与同义词，用于完善关键词及归一规则。

## 验收口径

已接入来源以数据库 `source_runs` 的成功状态和原文存档核对。禁用来源显示覆盖缺口，不计作“零公告”。14 天连续试运行从 2026-10-03 起计算，至少到 2026-10-16；届时再对漏采、公告编号、截止日、跨平台重复和变更做样本复核。
