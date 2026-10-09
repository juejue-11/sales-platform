# 成熟 Web 记录/详情布局调研

调研日：2026-10-09  
对象：矿业与工业设备销售团队的采购机会详情页。主要任务是阅读较长的采购原文、核对 AI 研判证据与方案，并记录人的决定。  
范围：只看产品官方文档及其官方截图；“适合本系统”的部分标为设计判断。

## 先看截图：四种成熟记录页的共同骨架

### Linear：文档主面，属性行就地编辑

Linear 的官方项目说明把短摘要、详细描述、外部资源、项目文档和里程碑放在同一条项目文档流中；项目属性紧跟摘要排列，可直接点击编辑。项目详情侧栏可从概览或 Issues 页面打开，侧栏也能查看或修改这些属性。[项目官方文档](https://linear.app/docs/projects)。

官方项目截图可见：标题与摘要占据主阅读面，`Properties / Initiatives / Labels / Resources / Customers` 是左对齐的属性行，资源用紧凑的链接条目排列，更新记录用一条边界清楚的区域承载；没有把每个字段再包成独立卡片。[项目概览截图](https://webassets.linear.app/images/ornj730p/production/5622bb57d625b2dbbe27639fe6f685e1c43589bd-2105x1332.png?w=1440&q=95&fm=png)；[属性就地编辑截图](https://webassets.linear.app/images/ornj730p/production/97431e2be69c1959a7759239cc709a965473c386-1573x1035.png?w=1440&q=95&fm=png)。

可迁移到采购详情：把公告正文、核实过的事实与 AI 解释当作主文档；把截止日期、采购方、规则门槛、负责人和 AI 版本做成一组可收起的属性行。侧栏应服务于核对和动作，而不是复制一份正文。

### Notion：标题固定，属性分区，右侧详情面板可开关

Notion 的官方布局说明把页面拆成 `Heading`、主页面、`Property group` 和右侧 `Details panel`；属性可以固定在标题下，也可以移到右侧面板，面板可打开或关闭。[Layouts 官方说明](https://www.notion.com/help/layouts#build-a-layout)。

官方布局截图显示标题与少量关键属性在左侧主面上，较多属性按分区收在属性组；右侧单独是页面设置或详情面板。[布局构建器截图](https://images.ctfassets.net/spoqsaf9291f/1JhmGEelQl3xaWh6KTvN1L/7f5e076fe8fa246f1739608d07060e86/Reference_visuals_Oct_22_Screenshot_1.png)；[详情面板开关截图](https://images.ctfassets.net/spoqsaf9291f/LdPgtqQpyygy9vFDd8ZTW/8a971e0454cbbdcd83bc79e08ce7c804/open_and_close_details.gif)。官方还提供 `Simple` 和 `Tabbed` 两种结构；Tabbed 会保留一个 `Content` 页签，再放置其他数据库视图。[结构说明](https://www.notion.com/help/layouts#structure)；[Tabbed 布局截图](https://images.ctfassets.net/spoqsaf9291f/5VR0XOwHpPQ3HyqjbmGjLc/acd6f076eaeeb01d92f1775fa998b552/tabbed_layout.gif)。

可迁移到采购详情：将“截止、门槛、状态”固定到标题附近，其他来源元数据放入可关闭属性面板。页签只用于真正不同的工作视图，例如“原文 / 研判 / 决定 / 记录”，不要把每一段正文都做成页签或嵌套卡片。Notion 官方说明还要求一个数据库内的页面共用同一布局；这提示系统应保持记录之间稳定的字段顺序。[布局适用范围](https://www.notion.com/help/layouts#save--apply-your-layout)。

### GitHub Issues：右栏字段，时间线记录变化

GitHub 官方文档明确说明，Issue 字段出现在右侧栏，与 assignee、labels、type 等系统字段并列；字段可以从 `Add field` 加入，修改自动保存。按类型固定的字段会自动出现在侧栏。[Issue 字段官方文档](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-and-managing-issue-fields)。

官方 Issue 侧栏截图显示字段是垂直的“字段名—值”行，行之间只有细分隔线；Assignees、Labels、Type、Projects 依次排列，没有每行一个阴影卡片。[Issue 侧栏截图](https://docs.github.com/assets/cb-15906/mw-1440/images/help/issues/issue-add-type.webp)。GitHub 同一文档规定，字段变化会进入 Issue timeline，并记录被改字段、新值、操作者和时间。[时间线记录说明](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-and-managing-issue-fields#viewing-field-changes-in-the-timeline)。

可迁移到采购详情：用同样的“字段行 + 细分隔线”呈现采购方、截止日期、规则门槛、负责人、当前决定；用轻量时间线记录 AI 重判、人工改状态和方案选择，确保能回到“谁在什么时候依据哪一版资料做了什么”。

### Salesforce Lightning：标题动作、亮点摘要、页签、活动时间线

Salesforce 官方可访问性说明描述了记录页的顺序：对象类型和记录名称作为页面标题，随后是记录动作栏，再是显示关键字段的 highlights panel；之后是页签，可切换详情、Chatter、相关列表和活动。[Lightning 记录页官方说明](https://help.salesforce.com/s/articleView?id=sf.accessibility_screen_reader.htm&language=en_US&type=5#Record_Page)。官方组件说明还允许 Highlights Panel 在宽区域横排、窄区域竖排，并支持收起第二行和限制可见动作数量。[Highlights Panel 说明](https://help.salesforce.com/s/articleView?id=sf.lightning_page_components.htm&language=en_US&type=5#Highlights_Panel)。

Salesforce 官方活动时间线截图把活动按 `Next Steps / Past Activities` 分组，用细竖线连接图标与条目，条目右侧给出日期，并用箭头展开细节。[活动视图官方说明](https://help.salesforce.com/s/articleView?id=sf.customize_records_record_activities_view.htm&language=en_US&type=5)；[活动时间线截图](https://sf-zdocs-cdn-prod.zoominsoftware.com/tdta-xcloud-setup-264-0-0-production-enus/06b096fc-5f18-4140-9448-45de7d877850/setup/images/record_page_activity_view_timeline.png)。

可迁移到采购详情：保留标题旁的少量高价值动作（重新分析、恢复机会、保存决定），把当前状态和截止放入一行亮点摘要；把“人工决定历史 / AI 重判历史 / 关联项目”做成细时间线或页签内容。Salesforce 的完整相关列表适合 CRM 全量管理，本系统只应取其动作栏、摘要栏和时间线这三个结构。

## 对比与取舍

| 产品 | 实际布局观察 | 对本系统有用的借鉴 | 应避免的部分 |
| --- | --- | --- | --- |
| Linear | 主面是标题、摘要、属性行、资源和更新；项目详情侧栏可开关 | 长采购原文作为主文档；属性/动作侧栏作为核对工具；资源链接保持一行 | 不照搬深色项目主题或把资源标签做成装饰性堆叠 |
| Notion | 标题可固定属性，属性组可在主面或右侧；支持 Simple/Tabbed | 将截止、门槛、AI 版本固定在标题附近；把来源元数据收进可关闭面板 | 不为每个信息块建立模块卡；页签只承载独立视图 |
| GitHub Issues | 右栏是字段行；字段变化进入时间线，带人和时间 | 用字段行表现记录元数据；用时间线追踪重判和人工决定 | 不把设置齿轮、组织权限等复杂管理控件带入销售工作流 |
| Salesforce Lightning | 标题→动作栏→亮点摘要→页签；活动用可展开时间线 | 顶部保留少量明确动作；用时间线呈现后续动作和历史 | 不复制完整 CRM 的相关列表和多层组件，避免详情页再次卡片化 |

## 设计判断：采用“Linear 主面 + 属性/动作侧栏”，吸收 GitHub/Salesforce 的时间线纪律

这是针对本销售系统的主张，不是对所有 Web 产品的普遍结论。销售人员需要连续阅读采购原文，再在同一上下文内核对 AI 证据、比较方案并保存人工决定；因此最重要的是一条可滚动、可引用的阅读主面，而不是多个同等重量的卡片。

- 主阅读面宽约 720–820px，标题、采购方、截止信息后直接进入“采购要求 / 原文证据”。AI 理由以“结论 + 证据摘录 + 来源记录号”紧跟在相关段落之后，保留上下文。
- 右侧 280–320px 作为可收起的属性/动作栏：状态、规则门槛、截止日期、采购方、负责人、AI 版本、当前人工决定和一个主要保存动作。属性采用字段行与细分隔线，支持就地编辑；侧栏关闭时主文档不变。
- 顶部只有一条动作栏和一条一级内联导航：`原文`、`研判`、`方案`、`决定`、`记录`。导航用于跳到主文档中的章节，或切换独立视图；不把每节再套 Card。
- “方案比较”用紧凑对照表或单层可选行；“人工决定”用表单行和保存状态；“历史”用 GitHub/Salesforce 风格的轻时间线。每层只承担一种任务，边界依靠留白、细线和标题，不依靠阴影卡片。
- 移动端先显示标题和截止，再显示原文；属性/动作栏改为“查看属性”抽屉或紧随标题的折叠段，方案和决定保持可操作，不横向滚动整页。

```text
┌──────────┬────────────────────────────────────────────┬───────────────┐
│ 左侧导航 │ ← 机会池 / 采购项目 #42       [重判] [更多] │ 属性与动作栏  │
│          │ 铜矿破碎机采购                         │ 状态            │
│ 机会池   │ 某矿业集团 · 截止 2026-11-03              │ 截止日期        │
│ 处理状态 │ 原文  研判  方案  决定  记录              │ 规则门槛        │
│ 设置     │──────────────────────────────────────────│ 采购方          │
│          │ 采购要求（长文阅读区，720–820px）          │ 负责人          │
│          │ 公告原文…… [证据 01]                       │ AI 版本/状态     │
│          │                                             │ [保存人工决定]  │
│          │ AI 研判：结论 + 证据摘录                     │                 │
│          │ 方案比较：单层对照行                         │                 │
│          │ 人工决定：决定 / 下一步 / 备注               │                 │
│          │ 记录：细时间线                               │                 │
└──────────┴────────────────────────────────────────────┴───────────────┘
```

### 设计令牌

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| 暗航墨 | `#172D48` | 左侧导航、关键标题、主要动作文字 |
| 纸白 | `#FFFFFF` | 主阅读面、输入区域 |
| 冷雾 | `#F3F6FA` | 应用背景，区分导航与正文 |
| 证据青 | `#216C67` | 已核实链接、主要保存动作、可操作焦点 |
| 截止琥珀 | `#AD6D17` | 临近截止、待复核、资料过期等语义状态 |
| 线灰 | `#D9E2E6` | 字段行、章节、时间线的细分隔线 |

字体使用本地中文无衬线：正文 `Microsoft YaHei, DengXian, sans-serif`，标题沿用同一族以保持连续阅读；正文 15–16px、行高约 1.75，长段落控制在约 70–80 个字符以内；日期、金额、编号使用等宽数字（`font-variant-numeric: tabular-nums`）。字段标签使用句式大小写和短动词，不用全大写眉标。

### 反卡片化检查

- 首屏只允许一个主阅读面和一个侧栏；章节之间用留白与 1px 细线，不用“卡片套卡片”。
- 重复记录用列表/时间线，方案用表格/单层选择行，原文用正文段落；只有需要独立操作或独立状态的区域才拥有边界。
- 阴影只用于真正浮出的抽屉、菜单或弹层；普通正文区域不加统一阴影和相同圆角。
- 标签只承载真实状态（截止、复核、已核实）；不把每个事实都染色为芯片。
