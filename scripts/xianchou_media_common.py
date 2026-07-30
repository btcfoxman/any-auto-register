from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
import re
from typing import Any


GENERIC_INTROS = {
    "",
    "以图生成视频的短片",
    "一段由人工智能生成的短片",
    "ai生成视频",
    "ai短片",
    "视频短片",
}

TAG_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("萌宠", ("猫", "狗", "宠物", "动物", "熊猫", "精灵")),
    ("战争", ("战争", "战场", "士兵", "军队", "坦克", "岳飞", "木兰")),
    ("末日", ("末日", "灾难", "废土", "毁灭", "丧尸")),
    ("犯罪", ("犯罪", "警察", "案件", "追捕", "黑帮", "凶手")),
    ("悬疑惊悚", ("悬疑", "惊悚", "恐怖", "诡异", "咒", "画廊", "狐缘")),
    ("科幻", ("科幻", "未来", "太空", "宇宙", "机器人", "机甲", "赛博", "骇客")),
    ("玄幻", ("玄幻", "修仙", "化龙", "神魔", "法术", "山海")),
    ("古装", ("古装", "武侠", "江湖", "宫廷", "古代", "驸马", "三国")),
    ("奇幻", ("奇幻", "魔法", "精灵", "龙", "神话", "梦境")),
    ("历史人文", ("历史", "文化", "非遗", "古诗", "长江", "古城", "人文")),
    ("游戏", ("游戏", "gta", "电竞", "炒股逆袭")),
    ("科技", ("科技", "人工智能", "ai时代", "超充", "数字", "云鼎")),
    ("知识科普", ("科普", "知识", "教学", "高考", "公益", "心理")),
    ("创意广告", ("广告", "品牌", "精华", "宣传", "tvc", "电商")),
    ("时尚", ("时尚", "服装", "穿搭", "模特", "美学")),
    ("复古", ("复古", "怀旧", "童年", "老机器", "书信")),
    ("风景", ("风景", "山川", "江城", "海口", "自然", "文旅", "秘境")),
    ("VLOG", ("vlog", "日常", "记录", "自我介绍")),
    ("情感", ("爱情", "亲情", "陪伴", "遗憾", "妈妈", "童年", "信徒")),
    ("都市现代", ("都市", "城市", "职场", "现代", "老板", "上岸")),
    ("娱乐", ("娱乐", "搞笑", "舞蹈", "音乐", "派对", "mv")),
    ("数字人", ("数字人", "虚拟人", "口播")),
    ("意识流", ("意识流", "意境", "抽象", "实验影像", "梦")),
)

INTRO_PATTERNS = (
    "《{title}》把{genre}作为画面底色，在人物行动、空间转换和情绪递进之间展开一段完整叙事。",
    "镜头从《{title}》的核心意象出发，以{genre}语汇串联角色、环境与关键转折。",
    "这段影像围绕《{title}》展开，借助{genre}表达观察人物关系与场景变化。",
    "作品以《{title}》为线索，把{genre}氛围融入动作节奏和视觉细节之中。",
    "在《{title}》里，画面通过{genre}式的光影与调度逐步揭示故事重点。",
    "《{title}》是一段侧重{genre}质感的短片，内容随人物处境与环境变化自然推进。",
    "从《{title}》这一命题延伸，影片以{genre}视角捕捉行动、情绪与空间之间的联系。",
    "影片用《{title}》建立叙事入口，让{genre}元素在连续镜头中形成清晰的情绪走向。",
    "《{title}》将人物处境放进具有{genre}气息的场景，依靠细节和节奏推动内容发展。",
    "围绕《{title}》展开的画面，把{genre}特征转化为角色行动与环境变化的线索。",
    "这部名为《{title}》的作品从具体场景切入，用{genre}风格呈现事件的起伏与余韵。",
    "《{title}》以富有{genre}感的镜头组织内容，重点落在主体变化和情节节点上。",
)

PROMPT_OPENINGS = (
    "以《{title}》的叙事矛盾作为开场抓手",
    "从《{title}》最有辨识度的场景进入",
    "抓住《{title}》里的核心人物与环境关系",
    "围绕《{title}》的主要意象重新组织画面",
    "把《{title}》的情绪转折放在镜头设计中心",
    "沿着《{title}》的事件线索推进视觉叙事",
    "选择《{title}》中最能建立氛围的瞬间起笔",
    "以《{title}》的空间变化带动内容展开",
    "让《{title}》的角色行动成为镜头衔接依据",
    "从《{title}》的关键细节向外延伸场景",
    "提炼《{title}》的主题线索并转成连续镜头",
    "把《{title}》的视觉记忆点置于叙事前景",
    "从《{title}》的关系变化切入人物调度",
    "依据《{title}》的节奏起伏安排镜头顺序",
    "先建立《{title}》独有的环境与人物状态",
    "用《{title}》的核心动作贯穿整段画面",
)

PROMPT_METHODS = (
    "用由远及近的景别交代空间，再通过细节特写完成情绪落点",
    "让稳定构图与短促运动镜头交替，突出事件推进时的力量变化",
    "以自然光影和环境层次区分前后段，使主体始终清楚可辨",
    "利用跟随、停顿和反打建立人物关系，避免场景转换失去方向",
    "把动作节点放在剪辑节拍上，并以环境声势强化临场感",
    "用色彩温度的变化提示情绪转折，让结尾保留可回味的停顿",
    "先呈现环境全貌，再将注意力收束到人物表情和关键物件",
    "以流畅的运动轨迹连接不同空间，保持时间和动作逻辑连续",
    "让前景遮挡、纵深和光线变化共同塑造画面的空间感",
    "控制镜头呼吸与动作密度，使重要情节拥有明确的视觉重音",
    "通过局部线索逐步补全信息，并在高潮处给出更开阔的视角",
    "将人物走位与环境变化同步设计，让每次转场都承接上一动作",
    "采用克制的镜头移动观察细节，在关键时刻加快节奏形成对比",
    "用清晰的视线关系和光影方向组织画面，增强叙事的可读性",
    "在连续动作中穿插环境反应，让场景本身参与情绪表达",
    "以具有质感的近景连接全景，兼顾人物状态与世界氛围",
)

PROMPT_ENDINGS = (
    "收束时回到最初的视觉线索，形成首尾呼应。",
    "结尾留出短暂静止，让情绪自然沉淀。",
    "最后用一个明确动作完成段落，而不是依赖文字解释。",
    "使高潮之后的环境变化成为余韵。",
    "让主体在结尾仍保持清晰，避免信息突然中断。",
    "最终以空间关系的变化回应前段铺垫。",
    "尾声保留关键物件或人物反应，延长故事想象。",
    "收尾镜头应简洁有力，并与整段色彩基调一致。",
    "让最后一个景别承担情绪总结，不重复前面的信息。",
    "结尾通过动作完成或视线停留给出清楚落点。",
    "在尾声降低剪辑密度，使核心意象得到充分呈现。",
    "最后把视角交还给环境，为故事保留开放感。",
)

PROCESS_OPENINGS = (
    "我先拆解《{title}》里真正推动内容的动作和关系",
    "这次从《{title}》的空间气质入手，而不是先套用固定风格",
    "处理《{title}》时，我把人物状态和环境信息分成两条线梳理",
    "我先找出《{title}》最值得保留的视觉记忆点",
    "为避免画面只停留在概念上，我从《{title}》的具体事件开始搭建",
    "这段内容先以《{title}》的情绪曲线确定镜头轻重",
    "我围绕《{title}》的核心意象做了由局部到整体的画面规划",
    "创作《{title}》时，先明确角色在每个场景中的目标和阻力",
    "我把《{title}》的主题压缩成可被镜头直接表现的几个节点",
    "针对《{title}》，先整理环境、人物和关键物件之间的呼应",
    "我从《{title}》最具冲突感的一刻反推前后的镜头安排",
    "这次用《{title}》的叙事节拍决定景别和运动方式",
    "我先为《{title}》建立清晰的时间顺序和视觉方向",
    "制作《{title}》时，首先排除与主题无关的装饰性画面",
    "我从《{title}》的开场信息量入手，逐步安排后续揭示",
    "为了保留《{title}》的独特气质，我先提炼素材本身的颜色和动作特征",
)

PROCESS_MIDDLES = (
    "随后落实这条镜头思路：{method}；让“{detail}”不只停留在文字说明",
    "画面阶段按以下原则推进：{method}；并把“{detail}”作为转场判断依据",
    "具体执行遵循“{method}”，重点呈现“{detail}”所包含的变化",
    "镜头组织以“{method}”为准，使“{detail}”成为可见的叙事线索",
    "之后将“{method}”落实到分镜，把“{detail}”拆成有前后因果的画面",
    "我再按照“{method}”校准节奏，确保“{detail}”能够被迅速理解",
    "中段通过“{method}”建立层次，让“{detail}”逐渐显露而非一次说尽",
    "实际剪辑贯彻“{method}”，同时保留“{detail}”里的细节密度",
    "分镜衔接遵循“{method}”，使“{detail}”拥有自然的起伏",
    "随后以“{method}”处理动作和光线变化，回应“{detail}”的内容重点",
    "画面推进执行“{method}”，让“{detail}”在不同景别中获得新的信息",
    "我用“{method}”处理段落关系，使“{detail}”既清晰又不过度直白",
)

PROCESS_ENDINGS = (
    "最后检查动作连续性，并让尾声停在最有余味的画面上。",
    "完成后重新平衡前后段信息量，让情绪有自然的进入和退出。",
    "收尾时删去重复镜头，只保留能回应主题的细节。",
    "最后统一光影方向和色彩层次，使不同场景属于同一段叙事。",
    "完成剪辑后再核对主体位置，确保视线不会在转场时丢失。",
    "结尾减少解释性信息，让环境和人物反应自行完成表达。",
    "最终以节奏复查代替模板化包装，使每个镜头都有明确作用。",
    "最后保留一处克制停顿，让关键情绪有落地的时间。",
    "完成后从头检查视觉线索，确认开场铺垫在尾声得到回应。",
    "尾段采用更简洁的构图，把注意力重新交还给核心意象。",
    "最后调整声音与画面重音，使高潮和余韵彼此分明。",
    "成片前再核对景别变化，避免连续镜头出现机械重复。",
)

NATURAL_LENSES = (
    "用一个交代空间的远景起步，随后把景别收紧到人物反应",
    "让镜头贴着主体移动，环境只在动作间隙逐步显露",
    "先观察局部材质和光线，再把完整场景交给观众",
    "保持机位克制，只在关系发生变化时移动视角",
    "从带有遮挡的前景望进去，让纵深慢慢打开",
    "以人物视线为轴安排正反景，避免无目的地切换角度",
    "用较长的观察镜头建立现场，再插入两三个短促细节",
    "让全景承担信息，近景只负责情绪，不重复解释情节",
    "从环境中的一个小变化切入，顺势找到真正的主体",
    "先把人物留在画面边缘，等关键动作出现再调整构图重心",
    "利用逆光和轮廓建立第一印象，之后回到自然、清楚的曝光",
    "让摄影机保持呼吸感，移动速度跟随人物而不是跟随音乐",
    "开头采用稳定构图，中段才释放手持感和更近的距离",
    "通过门框、树影或建筑线条限定视野，让场面更有方向",
    "以一次连续运动串起前后空间，减少为了热闹而产生的跳切",
    "让镜头先停留在结果，再慢慢补足导致它发生的线索",
)

NATURAL_MOTIONS = (
    "动作之间留半拍空隙，让因果关系能够被看清",
    "把节奏变化放在事件节点上，平段不必持续加速",
    "人物每次转身、停顿和触碰都承接上一镜的信息",
    "用环境的反应连接转场，使空间变化不显得突然",
    "高潮前缩短镜头，高潮后立刻放慢，给情绪一个出口",
    "重要动作完整呈现，次要信息则压缩在移动镜头里",
    "不依赖大幅度运镜，靠前后景关系制造推进感",
    "把声音重音与动作落点错开一点，避免节拍过于机械",
    "保留偶发的小动作和视线游移，让主体更像真实存在于现场",
    "通过明暗转换提示段落变化，而不是用生硬的特效转场",
    "前后镜头保持运动方向一致，必要的反转留到情绪拐点",
    "让剪辑跟随注意力移动，观众看完一个信息后再给下一个",
    "重复出现的物件只保留两次：第一次建立，第二次回应",
    "用一段安静观察托住中场，避免所有信息挤在同一节奏里",
    "把场景声当作转场桥梁，画面切换后仍保留短暂延续",
    "结尾前逐渐减少动作密度，让最后的变化更醒目",
)

NATURAL_ENDINGS = (
    "尾声停在人物尚未说尽的反应上",
    "最后把视角交还给环境，不再补充解释",
    "收在一个明确完成的动作上，干净离场",
    "让开场出现过的细节在最后一次短暂回到画面",
    "结尾只留下声音和静止构图，给余韵留出位置",
    "在情绪最高处之后多保留一个呼吸镜头",
    "最终景别比开场更远，让事件回到它所在的世界",
    "以人物视线的停留结束，不使用文字替观众下结论",
    "最后一个镜头保持简单，避免再引入新的信息",
    "让光线或天气的变化承担收束作用",
    "在动作结束前半秒切走，保留一点未完成感",
    "结尾回到关键物件，但换一个距离重新观看",
)

PROMPT_RENDERERS = (
    "{anchor}{lens}；{motion}，{ending}。",
    "别急着交代全貌，先给出关键情境：{anchor}{lens}。随后{motion}，{ending}。",
    "开场只给观众一个线索。{anchor}{motion}；{ending}。",
    "不加说明性字幕，直接进入画面。{anchor}{lens}，{ending}。",
    "{tone}应当成为底色。核心情境如下：{anchor}{lens}；{motion}。",
    "视觉重点不在堆叠奇观。{anchor}{motion}，{ending}。",
    "先听见现场，再看清发生了什么。{anchor}{lens}；{ending}。",
    "把它当成一段正在发生的事，而不是概念展示。{anchor}{lens}。{motion}。",
    "镜头可以晚一点抵达主体。{anchor}中段{motion}，{ending}。",
    "节奏采用“静—动—静”的走向。静处交代这件事：{anchor}动起来之后，{motion}。",
    "不要用旁白抢先解释。{anchor}{lens}，让画面自己补齐关系；{ending}。",
    "第一层先给事实，第二层才展开环境。{anchor}{motion}，{ending}。",
    "这段适合从结果倒推原因。{anchor}随后借环境变化还原过程，{ending}。",
    "构图保持{tone}，剪辑则不必一味平缓。{anchor}到转折处再让镜头明显靠近。",
    "画面关键词是{keywords}。{anchor}{lens}；{ending}。",
    "用一次连续观察进入现场。{anchor}不要立刻切走，等动作自然发生；{ending}。",
    "前半段让空间说话，后半段才交出事件核心。{anchor}{motion}。",
    "{anchor}色彩不必过度饱和，层次交给光线和距离；{ending}。",
    "从一个容易被忽略的细节出发，再逐步补全事件。{anchor}{motion}，不要用重复镜头补时长。",
    "先建立方向，再制造变化。{anchor}{lens}，直到关键动作完成才切换视角。",
    "把观众放在现场而不是讲述者身边。{anchor}{motion}，{ending}。",
    "开头保持节制。{anchor}中段允许节奏突然抬升，尾声则重新安静下来。",
    "不追求每一帧都饱满，给这件事留出负空间。{anchor}{lens}；{ending}。",
    "声音先行半拍，画面随后跟进。{anchor}{motion}。",
)

PROCESS_RENDERERS = (
    "初剪只保留真正推动内容的镜头。核心情境是：{anchor}第二遍再处理节奏，把无效的重复动作拿掉；{ending}。",
    "第一版关掉配乐，只看事件能否成立。{anchor}动作顺了以后才补环境声和音乐，最终{ending}。",
    "素材先按空间而不是按时间归类。主要内容如下：{anchor}重新排序后，{motion}。",
    "这次没有先套风格词。{anchor}先把事件剪清楚，再用{tone}的光影统一不同场景。",
    "分镜数量由动作决定。{anchor}需要完整呈现的地方少切，信息跳跃处才缩短镜头；{ending}。",
    "先做了一版克制的顺剪。{anchor}确认前后关系之后，复剪才加入运动和声音层次。",
    "构图阶段先看主体落点，避免每个镜头都居中。{anchor}具体处理时，{lens}。",
    "剪辑节拍没有贴死音乐。{anchor}声音稍晚进入，画面因此多了一点真实停顿。",
    "先从素材里挑出三个真正有用的变化。{anchor}剩余镜头只承担过渡，不抢重点。",
    "为了避免叙事发散，先把事件写成一条动作线。{anchor}每次转场都检查方向和视线，{ending}。",
    "色彩按场景分别校正，没有一开始就统一滤镜。{anchor}情绪走向确定后，再收拢整体色温。",
    "这段做了两次减法：先删解释性画面，再删重复特写。{anchor}留下的镜头只服务于这条主线。",
    "声音与画面分开处理。{anchor}现场声负责连接空间，音乐只在关系变化时抬高存在感。",
    "先用静帧检查视觉顺序。{anchor}确认不听对白也能看懂之后，才恢复动作并调整长度。",
    "开场试过几个版本，最后保留信息最少的那一个。{anchor}内容因此有了逐步显露的余地。",
    "处理重点放在转场两侧：上一镜把动作送出去，下一镜把它接回来。{anchor}事件没有被剪辑打断。",
    "先确定最安静和最激烈的两个点，再反推中间节奏。{anchor}整段因此有起伏，而非保持同一强度。",
    "镜头筛选以视线为准。{anchor}不能把注意力带向主线的素材都被移出，{ending}。",
    "这次刻意保留了一些不完美的小动作。{anchor}它们增强了真实感，也削弱过度设计的痕迹。",
    "画面先完成无声版本，再逐层加入对白、环境和音乐。{anchor}声音没有填满所有空隙，{ending}。",
    "节奏表只标事件，不标固定秒数。{anchor}停留多久由信息量决定，转折则尽量利落。",
    "前景、主体和背景分别检查了一遍。{anchor}三层关系稳定后，运动镜头里仍能看清重点。",
    "粗剪先忠于事件顺序，精剪才调整时间感。{anchor}所有省略都用动作或声音留下连接。",
    "没有用统一转场包解决衔接。{anchor}不同场景分别选择切、叠化或直接停顿，{ending}。",
)

PROCESS_RENDERERS_NO_ANCHOR = (
    "素材入轨前先静音通看一遍，只记录{keywords}的变化。粗剪完成后再恢复声音，{ending}。",
    "这条片没有预设固定镜头数。{tone}来自素材本身，动作完整就多停留，信息明确便及时切走。",
    "第一遍只标记视觉重音，第二遍处理前后因果。关于{keywords}的细节被留到最后校准，避免开场信息过满。",
    "先按“远景、行动、反应”给素材分组，再打乱原顺序寻找更自然的进入点。收尾阶段{ending}。",
    "剪辑从最安静的一组画面开始搭建，高潮镜头暂时放在一旁。骨架稳定后再把强动作放回正确位置。",
    "色彩和节奏分两轮处理：先保证每个空间内部一致，再用{keywords}的变化连接段落。",
    "没有让音乐决定每一次切点。动作和视线先形成自己的节奏，配乐只负责扩大已经存在的情绪。",
    "镜头取舍遵循一个标准：是否带来新信息。重复的角度即使漂亮也移出主线，{ending}。",
    "先把{keywords}分别做成三条短线，再观察它们在哪些时刻自然交会；成片顺序由这些交会点决定。",
    "初剪保留了较多呼吸，复剪才压缩停顿。这样{tone}不会变成拖沓，动作也不显得匆忙。",
    "转场没有统一套用效果。相邻画面若能靠方向、色块或声音接住，就直接切；接不住才留出停顿。",
    "画面层次按前景、主体、背景逐项复核。{keywords}都能被看清之后，才开始调整整体速度。",
    "先做无字幕版本检查可读性，再决定哪些信息需要声音补足。最终保留的文字只承担必要提示。",
    "这一版刻意减少华丽运镜，把注意力放在{keywords}。机位变化只发生在关系或情绪真正改变时。",
    "粗剪忠于现场顺序，精剪再重组时间。省略处用环境声延续，观众不会因为跳时而失去方向。",
    "先找出最适合作为入口和出口的两帧，中间段落再逐步填入。整个过程以{tone}为准，不强求均匀节拍。",
)

# These composable clauses deliberately keep content, visual direction, and
# workflow in the same sentence.  That avoids a library of reusable standalone
# sentences showing through when hundreds of metadata files are read together.
PROMPT_NATURAL_ENTRIES = (
    "{anchor}；",
    "先观察{k1}如何改变{k2}，再进入这一情境：{anchor}；",
    "让{k3}先于主体出现，随后才交代事件核心：{anchor}；",
    "不把{tone}做成表面滤镜，而是用来表现这一情境：{anchor}；",
    "从{k2}最细微的变化开始，逐步补全事件：{anchor}；",
    "先听{k1}带来的现场感，再看完整过程：{anchor}；",
    "如果把{k1}当作入口，{k2}就负责扩展空间，事件本身是{anchor}；",
    "第一帧只回答“在哪里”，随后才补齐这件事：{anchor}；",
    "画面先留意{k3}，不立即给出结果，事件线索是{anchor}；",
    "把{k2}作为场景之间的桥，两端连接的内容是{anchor}；",
    "不对所有画面平均用力，而是先认清主要情境：{anchor}；",
    "先用{k3}建立视线移动，之后才交代这一场面：{anchor}；",
    "不依赖解释性字幕，让{k1}和{k3}自行暗示前后关系：{anchor}；",
    "从{tone}的环境状态出发，先让{k2}建立空间，再交出关键动作：{anchor}；",
    "开场少给结论，把{k1}和{k2}留给观众发现，主要情境是{anchor}；",
    "用{k1}承担事件进展，{k2}负责空间变化，{k3}只在情绪拐点出现：{anchor}；",
)

PROMPT_NATURAL_DIRECTIONS = (
    "整体保持{tone}，镜头{lens}，过程中{motion}，收尾时{ending}",
    "机位选择{lens}，在{k1}发生变化时{motion}，到尾声再{ending}",
    "视角上{lens}，{k2}和{k3}不同时塞满画面，节奏遵循{motion}，最后{ending}",
    "只在{k1}与{k3}的关系改变时靠近，其余时候{lens}，中后段{motion}，并以{ending}作结",
    "先用{k2}确认观众位置，拍摄时{lens}，动作连接时{motion}，尾声{ending}",
    "不追求持续刺激，而是{lens}，等{k3}带来转折再{motion}，最后{ending}",
    "让{k1}、{k2}、{k3}分层显露，形式上{lens}，剪辑中{motion}，尾声{ending}",
    "色彩不过度饱和，镜头{lens}，通过{k1}和{k2}的相对变化做到{motion}，最后{ending}",
    "让全景承担{k2}，近景只保留{k1}的情绪，具体机位{lens}，节奏上{motion}，结尾{ending}",
    "镜头{lens}，不用旁白重述事件，而由{k3}回应{k1}，同时{motion}，尾声{ending}",
    "把{tone}作为停留尺度，不作表面修饰，画面{lens}，转折前{motion}，最后{ending}",
    "从{k2}的状态判断镜头长度，视角{lens}，在{k1}与{k3}交汇时{motion}，尾声{ending}",
)

PROCESS_NATURAL_ENTRIES = (
    "素材入轨前先静音通看，只记录{k1}、{k2}和{k3}的变化",
    "镜头数量没有预设，涉及{k1}的动作完整就多留一会，与{k2}有关的信息明确便及时切走",
    "第一遍标记{k1}的视觉重音，第二遍才梳理{k2}的前后因果",
    "素材按空间、行动和反应分组，{k1}负责进入，{k2}承担变化，{k3}作为退出信号",
    "剪辑骨架从{k2}最安静的部分搭起，强动作等{k1}和{k3}的关系稳定后再归位",
    "色彩和节奏分两轮处理，先保证{k1}所在的空间自洽，再用{k2}与{k3}连接段落",
    "音乐不决定切点，与{k1}有关的动作和由{k2}引出的视线先形成自己的节奏",
    "取舍镜头时只判断它是否带来新信息，重复表现{k1}的角度即使漂亮也移出主线",
    "先把{k1}、{k2}和{k3}各自做成一条短线，再找它们自然交会的时刻",
    "初剪给{k1}留足呼吸，复剪才压缩{k2}之间的无效停顿",
    "转场不统一套用效果，涉及{k1}的画面靠方向接住，{k2}使用声音延续，{k3}无法衔接时才留停顿",
    "画面按前景、主体和背景逐项复核，先确认{k1}可读，再调整{k2}与{k3}的相对强度",
    "无字幕版本先检查{k1}的可读性，之后才决定{k2}的哪些信息需要声音补足",
    "主动减少华丽运镜，把注意力放回{k1}、{k2}和{k3}，机位只在关系改变时移动",
    "粗剪先忠于现场顺序，以{k1}作为判断，精剪再用{k2}重组时间，省略处保留{k3}的环境声",
    "先从与{k1}有关的画面中找出最适合作为入口的一帧，再用{k3}选定退出画面，中间根据{k2}逐步填入",
    "粗剪先把{k1}、{k2}和{k3}分层，只保留能改变前后关系的镜头",
    "构图阶段先看{k3}将注意力带向哪里，避免每个镜头都把主体居中",
    "调色时先分场景校正{k1}与{k2}，等{k3}的情绪方向确定后才收拢整体色温",
    "剪辑做两轮减法，第一轮删掉解释{k1}的画面，第二轮删掉重复{k2}的特写",
)

PROCESS_NATURAL_STEPS = (
    "之后恢复声音，复剪时{motion}，整体保持{tone}，尾声{ending}",
    "然后用{k3}保持前后连续，具体机位{lens}，最后{ending}",
    "再把{k3}留到最后校准，避免开场信息过满，节奏处理遵循{motion}",
    "重排时{lens}，并使{k2}承接{k3}，收尾处{ending}",
    "骨架稳定后再放回强动作，整体保持{tone}，镜头选择{lens}",
    "之后按{k1}的信息量调整停留，转折处{motion}，最后{ending}",
    "再让配乐只扩大{k3}已经带来的情绪，不填满所有空隙，尾声{ending}",
    "随后让{k2}与{k3}保留清楚因果，镜头{lens}，复剪时{motion}",
    "成片顺序由这些交会点决定，不追求均匀节拍，收在{ending}",
    "这样{tone}不会变成拖沓，动作也不显匆忙，具体视角{lens}",
    "相邻画面能靠方向、色块或声音接住就直接切，同时{motion}，尾声{ending}",
    "最后根据{tone}的走向改变整体速度，运动镜头里仍保持{k1}清晰，收尾处{ending}",
    "最终文字只提示{k3}，不替画面解释结论，处理时{motion}",
    "其余时候{lens}，节奏改变只发生在{k1}与{k2}的关系转折处，尾声{ending}",
    "跳时之后仍保留清楚方向，并且{motion}，最后{ending}",
    "整个过程以{tone}为准，不强求均匀节拍，镜头选择{lens}",
)

PROFILE_WORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "战争": (("压迫而克制", "粗粝紧张", "冷峻肃穆"), ("烟尘", "冲突", "方向")),
    "末日": (("荒凉低沉", "紧绷陌生", "冷硬孤寂"), ("废墟", "生存", "尺度")),
    "悬疑惊悚": (("不安含蓄", "幽暗迟疑", "克制诡谲"), ("线索", "阴影", "停顿")),
    "科幻": (("清冷精确", "陌生明亮", "理性疏离"), ("空间", "材质", "未知")),
    "玄幻": (("庄重奇诡", "宏阔神秘", "凌厉飘逸"), ("法力", "山川", "对峙")),
    "古装": (("沉静古雅", "苍凉厚重", "含蓄凌厉"), ("衣袂", "礼序", "江湖")),
    "情感": (("温柔克制", "细腻安静", "温暖留白"), ("关系", "目光", "距离")),
    "萌宠": (("轻快生动", "亲近俏皮", "自然松弛"), ("动作", "表情", "陪伴")),
    "风景": (("开阔舒缓", "清透安静", "辽阔从容"), ("地貌", "天气", "光线")),
    "创意广告": (("简洁明快", "精致利落", "鲜明有序"), ("产品", "质感", "记忆点")),
    "意识流": (("流动朦胧", "自由抽象", "梦境般松散"), ("联想", "材质", "节奏")),
    "视觉艺术": (("有层次但不过饰", "清晰而富有质感", "克制且具有变化"), ("构图", "光影", "运动")),
}


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_rng(*parts: Any) -> random.Random:
    digest = hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def parse_har_projects(path: Path) -> list[dict[str, Any]]:
    import base64

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    projects: dict[str, dict[str, Any]] = {}
    for entry in payload.get("log", {}).get("entries", []):
        content = entry.get("response", {}).get("content", {})
        text = content.get("text") or ""
        if content.get("encoding") == "base64":
            text = base64.b64decode(text).decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except (TypeError, ValueError):
            continue
        if not isinstance(body, dict) or not isinstance(body.get("projects"), list):
            continue
        for item in body["projects"]:
            if not isinstance(item, dict):
                continue
            project_id = normalize_text(item.get("id"))
            if project_id and project_id not in projects:
                projects[project_id] = dict(item)
    return list(projects.values())


def segment_plan(duration: float, *, threshold: float = 180.0, target: float = 80.0, minimum: float = 60.0) -> list[tuple[float, float]]:
    if duration <= threshold:
        return [(0.0, duration)]
    count = max(2, int(math.floor(duration / target + 0.5)))
    while count > 2 and duration / count < minimum:
        count -= 1
    width = duration / count
    if width < minimum:
        raise RuntimeError(f"Cannot split {duration:.3f}s into segments of at least {minimum:.3f}s")
    return [(index * width, duration - index * width if index == count - 1 else width) for index in range(count)]


def infer_tag_title(title: str, description: str, source_tags: list[Any] | None = None) -> str:
    haystack = " ".join([title, description, *(normalize_text(value) for value in source_tags or [])]).lower()
    for tag, keywords in TAG_KEYWORDS:
        if any(keyword.lower() in haystack for keyword in keywords):
            return tag
    return "视觉艺术"


def _genre_phrase(tag_title: str, source_tags: list[Any] | None) -> str:
    labels = [normalize_text(value) for value in source_tags or [] if normalize_text(value)]
    if labels:
        return "、".join(labels[:2])
    return tag_title or "视觉叙事"


def is_generic_intro(value: Any) -> bool:
    text = normalize_text(value)
    return text.lower() in GENERIC_INTROS or len(text) < 5


def build_intro(*, title: str, description: str, source_tags: list[Any] | None, seed: Any) -> str:
    description = normalize_text(description)
    if description and description != normalize_text(title) and not is_generic_intro(description):
        return description[:1200]
    tag_title = infer_tag_title(title, description, source_tags)
    rng = stable_rng("intro", seed, title, description, source_tags)
    return rng.choice(INTRO_PATTERNS).format(title=title, genre=_genre_phrase(tag_title, source_tags))[:1200]


def _content_anchor(title: str, detail: str) -> str:
    text = normalize_text(detail)
    clean_title = normalize_text(title)
    if clean_title:
        base_title = re.sub(r"（\d+/\d+）$", "", clean_title)
        candidates = {clean_title, base_title}
        for candidate in candidates:
            wrapped_values = {candidate, f"《{candidate}》", f"“{candidate}”", f'"{candidate}"'}
            for wrapped in sorted(wrapped_values, key=len, reverse=True):
                if len(wrapped) >= 4:
                    text = text.replace(wrapped, "这段故事")
        if text.startswith(clean_title):
            text = text[len(clean_title) :].lstrip("：:，,。 -—")
    replacements = {
        "这部名为这段内容的作品": "这段作品",
        "这部名为这段故事的作品": "这段作品",
        "这段影像围绕这段内容展开": "画面沿着主要事件展开",
        "镜头从这段内容的核心意象出发": "镜头从核心意象出发",
        "围绕这段内容展开的画面": "画面",
        "这段内容将": "画面将",
        "这段故事将": "画面将",
        "这段内容是一段": "这是一段",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Source descriptions often repeat a display title before the actual
    # synopsis.  It is not useful inside prompt/process text.
    text = re.sub(r"^《[^》]{1,80}》(?:漫剧简介|作品简介|简介)?\s*", "", text)
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]
    if not parts:
        return "人物、环境与关键动作之间的变化"
    anchor = "".join(parts[:2]).strip()
    max_length = 220 if sum(character.isascii() for character in anchor) / max(len(anchor), 1) > 0.65 else 160
    if len(anchor) > max_length:
        shortened = anchor[:max_length]
        break_at = max(shortened.rfind(mark) for mark in ("。", "！", "？", "；", "，", ".", "!", "?", ";", ","))
        if break_at >= 90:
            shortened = shortened[:break_at]
        elif " " in shortened:
            shortened = shortened.rsplit(" ", 1)[0]
        anchor = shortened.rstrip("，,；;：: ")
    return anchor.rstrip("。！？!?；;. ")


def _inline_anchor(title: str, detail: str) -> str:
    text = _content_anchor(title, detail)
    text = re.sub(r"[。！？!?]+", "，", text)
    return text.strip("，, ") or "人物、环境与关键动作之间的变化"


def _remove_title_mentions(text: str, title: str) -> str:
    display_title = normalize_text(title)
    base_title = re.sub(r"（\d+/\d+）$", "", display_title).strip()
    candidates = {
        display_title,
        base_title,
        display_title.strip("《》"),
        base_title.strip("《》"),
    }
    for candidate in sorted((value for value in candidates if len(value) >= 3), key=len, reverse=True):
        for wrapped in (f"《{candidate}》", f"“{candidate}”", f'"{candidate}"', candidate):
            text = text.replace(wrapped, "这段内容")
    text = re.sub(r"《+这段内容》+", "这段内容", text)
    text = re.sub(r"(?:这段内容)(?:这段内容)+", "这段内容", text)
    return normalize_text(text)


def _polish_generated_text(text: str) -> str:
    replacements = {
        "镜头让镜头": "镜头",
        "视角上让镜头": "视角上，镜头",
        "具体机位让全景": "具体机位以全景",
        "镜头让全景": "让全景",
        "视角让全景": "视角采用全景",
        "镜头选择让镜头": "镜头",
        "镜头选择通过": "拍摄时通过",
        "最后结尾": "最后",
        "尾声结尾": "尾声",
        "最后最后": "最后",
        "最终最终": "最终",
        "收在在情绪": "停在情绪",
        "收在结尾": "结尾",
        "收尾处结尾": "收尾处",
        "尾声最后把": "尾声把",
        "收尾处最后把": "收尾处把",
        "收尾时最后把": "收尾时把",
        "结尾最终景别": "结尾的最终景别",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return normalize_text(text)


def _quoted_title(title: str) -> str:
    text = normalize_text(title)
    return text if text.startswith("《") else f"《{text}》"


def _profile(title: str, detail: str, source_tags: list[Any] | None, rng: random.Random) -> tuple[str, str]:
    tag_title = infer_tag_title(title, detail, source_tags)
    tones, keywords = PROFILE_WORDS.get(tag_title, PROFILE_WORDS["视觉艺术"])
    return rng.choice(tones), "、".join(rng.sample(list(keywords), k=len(keywords)))


def _segment_note(index: int, count: int, rng: random.Random, *, process: bool) -> str:
    if count <= 1:
        return ""
    if index == 1:
        choices = (
            "这一段负责建立人物和空间，先不透露后面的结果。",
            "作为开篇，信息应逐层出现，给后续冲突留下余地。",
            f"全片共{count}段，这一段只完成关系和环境的建立。",
            "段尾保留一个尚未解决的动作，方便下一部分自然接入。",
        )
    elif index == count:
        choices = (
            "这是收束段，前面出现过的动作和细节需要在这里得到回应。",
            "最后一段不再扩展新线索，重点完成已有情绪的落点。",
            f"来到第{index}/{count}段，剪辑逐渐减少信息量，把结尾留给人物反应。",
            "这一部分承担尾声，节奏应在高潮之后稳稳降下来。",
        )
    else:
        choices = (
            "本段处在故事中部，既要承接上一动作，也要把新的变化送往下一段。",
            "中段不重复开场信息，直接推进人物处境和事件压力。",
            f"第{index}/{count}段承担转折，入口与出口都保留连续动作。",
            "这一部分的任务是改变关系，而不是再次介绍人物。",
        )
    note = rng.choice(choices)
    if process and rng.randrange(2):
        note = note.replace("信息应逐层", "信息在复剪时逐层")
        note = note.replace("节奏应在", "节奏在复剪时应在")
        note = note.replace("需要在这里", "在剪辑中需要在这里")
    return note


def build_prompt(*, title: str, detail: str, source_tags: list[Any] | None, seed: Any, segment_index: int = 1, segment_count: int = 1) -> str:
    rng = stable_rng("prompt", seed, title, detail, segment_index, segment_count)
    anchor = _inline_anchor(title, detail)
    tone, keywords = _profile(title, detail, source_tags, rng)
    k1, k2, k3 = keywords.split("、")
    entry = rng.choice(PROMPT_NATURAL_ENTRIES).format(
        anchor=anchor,
        tone=tone,
        k1=k1,
        k2=k2,
        k3=k3,
    )
    direction = rng.choice(PROMPT_NATURAL_DIRECTIONS).format(
        lens=rng.choice(NATURAL_LENSES),
        motion=rng.choice(NATURAL_MOTIONS),
        ending=rng.choice(NATURAL_ENDINGS),
        tone=tone,
        k1=k1,
        k2=k2,
        k3=k3,
    )
    note = _segment_note(segment_index, segment_count, rng, process=False).rstrip("。")
    segment = f"；作为第{segment_index}/{segment_count}段，{note}" if note else ""
    text = f"{entry}{direction}{segment}。"
    text = _polish_generated_text(_remove_title_mentions(text, title))
    return text[:1200]


def build_creation_process(*, title: str, detail: str, seed: Any, segment_index: int = 1, segment_count: int = 1) -> str:
    rng = stable_rng("process", seed, title, detail, segment_index, segment_count)
    anchor = _inline_anchor(title, detail)
    tone, keywords = _profile(title, detail, None, rng)
    k1, k2, k3 = keywords.split("、")
    entry = rng.choice(PROCESS_NATURAL_ENTRIES).format(k1=k1, k2=k2, k3=k3)
    step = rng.choice(PROCESS_NATURAL_STEPS).format(
        lens=rng.choice(NATURAL_LENSES),
        motion=rng.choice(NATURAL_MOTIONS),
        ending=rng.choice(NATURAL_ENDINGS),
        tone=tone,
        k1=k1,
        k2=k2,
        k3=k3,
    )
    # Roughly one quarter of records mention the actual scene; the others read
    # as genuine editing notes instead of repeating prompt content.
    context = f"，本次素材中{anchor}" if rng.randrange(4) == 0 else ""
    note = _segment_note(segment_index, segment_count, rng, process=True).rstrip("。")
    segment = f"；第{segment_index}/{segment_count}段另外要求：{note}" if note else ""
    text = f"{entry}{context}；{step}{segment}。"
    return _polish_generated_text(_remove_title_mentions(text, title))[:1200]


def build_metadata(
    *,
    title: str,
    description: str,
    source_tags: list[Any] | None,
    seed: Any,
    tag_catalog: dict[str, dict[str, str]],
    segment_index: int = 1,
    segment_count: int = 1,
) -> dict[str, Any]:
    base_title = normalize_text(title) or "未命名影像"
    display_title = f"{base_title}（{segment_index}/{segment_count}）" if segment_count > 1 else base_title
    intro = build_intro(title=base_title, description=description, source_tags=source_tags, seed=seed)
    prompt = build_prompt(
        title=base_title,
        detail=intro,
        source_tags=source_tags,
        seed=seed,
        segment_index=segment_index,
        segment_count=segment_count,
    )
    tag_title = infer_tag_title(base_title, intro, source_tags)
    tag = dict(tag_catalog.get(tag_title) or tag_catalog["视觉艺术"])
    return {
        "title": display_title[:80],
        "intro": intro,
        "prompt": prompt,
        "tag_infos": [tag],
        "creation_process_text": build_creation_process(
            title=display_title,
            detail=intro,
            seed=seed,
            segment_index=segment_index,
            segment_count=segment_count,
        ),
    }
