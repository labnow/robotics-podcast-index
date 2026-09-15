Yes. For your goal, I would **skip XYZRank almost entirely** and build an **episode-level robotics / embodied-AI corpus directly from 小宇宙**, then optionally use Apple/RSS to fill gaps.

The key discovery is that 小宇宙’s internal search supports **episode search directly**:

```text
POST /v1/search/create

{
  "keyword": "...",
  "type": "EPISODE"
}
```

and supports pagination via `searchId` + `loadMoreKey`. This is documented by reverse-engineering projects rather than an official developer API. ([GitHub][1])

## What I would collect

For you, “robotics / embodied AI” should be broader than simply searching `机器人` and `具身智能`. Otherwise you will miss episodes such as discussions about VLA, manipulation, sim2real, robot foundation models, teleoperation, synthetic data, etc.

I would search roughly these keyword groups:

```text
# Core
机器人
robotics
具身智能
embodied AI
embodied intelligence
Physical AI

# Robot forms
人形机器人
humanoid
双足机器人
四足机器人
机器狗
机械臂
robot arm
移动机器人
mobile robot
服务机器人
工业机器人

# Foundation models / learning
VLA
Vision Language Action
机器人基础模型
robot foundation model
generalist robot
通用机器人
端到端机器人
机器人大模型
具身大模型
world model
世界模型

# Manipulation
机器人操作
robot manipulation
manipulation
dexterous manipulation
灵巧手
灵巧操作
grasping
抓取

# Training / data
模仿学习
imitation learning
强化学习
reinforcement learning
robot learning
机器人学习
具身数据
机器人数据
遥操作
teleoperation
示教
数据采集
synthetic data
合成数据
sim2real
sim-to-real
仿真机器人

# Navigation / perception
SLAM
机器人导航
robot navigation
视觉导航
visual navigation
具身感知
embodied perception
active perception

# Important platforms/projects
Isaac Sim
Isaac Lab
MuJoCo
Gazebo
LeRobot
ALOHA
Open X-Embodiment
RT-1
RT-2
RT-X
OpenVLA
π0
pi0
GR00T
GR00T N1
Cosmos
RoboCasa
RoboMimic

# Companies / projects worth tracking
宇树
Unitree
银河通用
Galbot
智元机器人
AgiBot
傅利叶
Fourier
Figure
Figure AI
Physical Intelligence
1X
Apptronik
Agility Robotics
Boston Dynamics
Tesla Optimus
Optimus
Sanctuary AI
Covariant
Skild AI
Field AI
```

You would then **union and deduplicate by episode ID**.

This is much better than category filtering because robotics episodes frequently appear inside technology, investment, VC, business, AI and general-interest podcasts.

---

## Why this will work quite well

小宇宙 already has a dedicated **“机器人专题”** aggregating relevant episodes. For example, it includes 硅谷101's episode on 3D data and embodied intelligence. ([XiaoYuzhou][2])

But keyword search discovers a much broader universe. Current public examples include:

* *EP23：具身智能专栏｜宇树上市3000亿，还没见着落地？* — covers RT-2, VLA, scaling laws, world models and robot training data. ([XiaoYuzhou][3])
* *机器人会做家务了吗？聊聊具身智能最真实的技术困境* — VLA, world models, manipulation, perception and control. ([XiaoYuzhou][4])
* 中金研究院's interview with 自变量机器人 CEO 王潜 — end-to-end models, scaling laws and Sim2Real. ([XiaoYuzhou][5])
* 张小珺's interview with 光轮智能 CEO 谢晨 — specifically simulation, synthetic data and Sim2Real for robot training. ([XiaoYuzhou][6])

So this is exactly the kind of corpus you want.

---

# More importantly: don't treat keyword hits equally

I'd collect **everything broadly**, and then run a second-stage relevance classifier.

For every episode, save:

```text
episode_id
podcast_id
podcast_name

title
description / show_notes

published_at
duration

play_count
comment_count

xiaoyuzhou_url

matched_keywords[]
relevance_score
topics[]
```

Then have an LLM classify:

```text
ROBOTICS_RELEVANCE

3 = primarily about robotics / embodied AI
2 = substantial robotics section
1 = robotics mentioned incidentally
0 = unrelated
```

And assign topics such as:

```text
humanoid
manipulation
locomotion
VLA
foundation_model
world_model
robot_data
simulation
sim2real
teleoperation
reinforcement_learning
imitation_learning
navigation
perception
hardware
actuator
startup
investment
commercialization
```

For your use case I would retain:

```text
relevance >= 2
```

This solves an important problem.

An episode called:

> **机器人遭遇数据荒？**

is trivial to find.

But an episode called something like:

> **从Scaling Law到物理世界**

could be an excellent robotics episode while containing neither `机器人` nor `具身智能` in its title.

You need **show notes + semantic classification**, not only title search.

---

# I would use 小宇宙 as the primary dataset

For this particular task I'd rank the sources:

```text
                    importance

小宇宙 episode search      ★★★★★
小宇宙 episode metadata    ★★★★★
Podcast RSS               ★★★★☆
Apple Podcasts            ★★★☆☆
XYZRank                    ★☆☆☆☆
```

Why?

XYZRank appears primarily optimized around **podcast ranking/metrics**, while you want **individual episodes**.

小宇宙's public episode pages expose remarkably useful information including:

```text
title
podcast
publish date
duration
show notes
play count
comment count
```

For example, its public page for a recent robotics episode exposes both playback and comment counts alongside the complete description. ([XiaoYuzhou][7])

That gives you something Apple/RSS normally cannot provide: **engagement**.

You could therefore eventually rank your robotics corpus by:

```text
relevance
×
recency
×
log(play_count)
×
engagement
```

instead of just chronological order.

---

# Your resulting database could be very useful

Imagine a local page like:

```text
Robotics Podcast Intelligence
────────────────────────────────────────

2026-08-...

★★★★★ 具身智能的“山寨机时代”
脑放电波
Topics: humanoid / commercialization / WRC
plays: ...
51 min


★★★★★ E244｜机器人走错路了？
硅谷101
Topics:
3D data / foundation models / embodied AI
77 min


★★★★★ 机器人会做家务了吗？
SmallWOD
Topics:
VLA / world model / manipulation
49 min


★★★★★ 机器人遭遇数据荒？
张小珺Jùn
Topics:
simulation / synthetic data / Sim2Real
101 min
```

Then you can filter:

```text
[technical only]

[VLA]
[simulation]
[robot data]
[humanoid]
[manipulation]
[startups]
[investment]

2026 only
> 5,000 plays
> 30 min
```

For someone following embodied-AI professionally, this would be considerably more useful than XYZRank itself.

### One limitation about “all”

There is no reliable way to guarantee **literally every robotics-related episode ever published**, because semantic relevance isn't equivalent to a finite keyword query, and 小宇宙's search API is undocumented.

But we can get very close with:

**many high-recall searches → deduplicate → retrieve complete show notes → semantic classification.**

That is the architecture I'd use rather than maintaining a manually curated list.

If you want, the next useful step is for me to **write the actual Python collector** that searches 小宇宙 across these terms, paginates the results, deduplicates episodes, and outputs a CSV/SQLite database of robotics episodes.

[1]: https://github.com/zdhgreat/xiaoyuzhou_script_skill/blob/main/reference.md?utm_source=chatgpt.com "xiaoyuzhou_script_skill/reference.md at main · zdhgreat/xiaoyuzhou_script_skill · GitHub"
[2]: https://www.xiaoyuzhoufm.com/podcast-topic/692fee198dc8fff6d044cfd1?utm_source=chatgpt.com "机器人专题 | 小宇宙 - 听播客，上小宇宙"
[3]: https://www.xiaoyuzhoufm.com/episode/6a8281f817676351c572fd67?utm_source=chatgpt.com "EP23：具身智能专栏｜宇树上市3000亿，还没见着落地？ - Her Alpha | 超额姐妹 | 小宇宙 - 听播客，上小宇宙"
[4]: https://www.xiaoyuzhoufm.com/episode/6a453a3efd8019cde5621e81?utm_source=chatgpt.com "机器人会做家务了吗？聊聊具身智能最真实的技术困境 - SmallWOD时差同频 | 小宇宙 - 听播客，上小宇宙"
[5]: https://www.xiaoyuzhoufm.com/episode/6979b170f653bd50d61d4560?utm_source=chatgpt.com "episode 48｜中金机器人播客 王潜：中国具身智能赛道没有“过热”而是“过冷” - 中金研究院 | 小宇宙 - 听播客，上小宇宙"
[6]: https://www.xiaoyuzhoufm.com/episode/68767e4c93fd2d72b8607c80?utm_source=chatgpt.com "109. 机器人遭遇数据荒？与谢晨聊：仿真与合成数据、Meta天价收购和Alexandr Wang - 张小珺Jùn｜商业访谈录 | 小宇宙 - 听播客，上小宇宙"
[7]: https://www.xiaoyuzhoufm.com/episode/6a8ea481ef65145dfcc5b41c?utm_source=chatgpt.com "E47 机器人会跳舞会打拳，离打工赚钱还有多远？ - 人间钱话 | 小宇宙 - 听播客，上小宇宙"
