import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from collector.eligibility import (
    REJECTION_LABELS,
    notice_rejection,
    publication_rejection,
)


LOCAL = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=LOCAL)


class EligibilityTests(unittest.TestCase):
    def test_publication_window_is_local_and_inclusive(self):
        self.assertIsNone(publication_rejection("2026-09-16", now=NOW))
        self.assertEqual(publication_rejection("2026-09-15", now=NOW), "published_too_old")
        self.assertIsNone(publication_rejection("2026-10-16", now=NOW))
        self.assertEqual(publication_rejection("2026-10-17", now=NOW), "published_in_future")

    def test_publication_timezone_and_missing_or_invalid_dates(self):
        utc_now = datetime(2026, 10, 16, 4, 0, tzinfo=ZoneInfo("UTC"))
        self.assertIsNone(publication_rejection("2026-10-16 11:59+08:00", now=utc_now))
        self.assertEqual(
            publication_rejection("2026-10-16 12:01+08:00", now=utc_now),
            "published_in_future",
        )
        self.assertEqual(publication_rejection(None, now=NOW), "missing_published_at")
        self.assertEqual(publication_rejection("not a date", now=NOW), "invalid_published_at")
        self.assertIsNone(publication_rejection("2026-10-16", now=NOW.replace(tzinfo=None)))
        with self.assertRaises(ValueError):
            publication_rejection("2026-10-16", now=NOW, max_age_days=True)
        with self.assertRaises(ValueError):
            publication_rejection("2026-10-16", now=NOW, max_age_days=0)

    def test_date_only_submission_is_valid_whole_local_day(self):
        title = "破碎机备件采购公告"
        self.assertIsNone(
            notice_rejection(title, "投标截止时间：2026-10-16", "procurement", "2026-10-16", now=NOW)
        )
        self.assertEqual(
            notice_rejection(title, "投标截止时间：2026-10-15", "procurement", "2026-10-16", now=NOW),
            "submission_expired",
        )
        self.assertEqual(
            notice_rejection(
                title,
                "投标文件应于2026年10月15日 09:00之前递交",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            "submission_expired",
        )

    def test_submission_deadline_clock_and_document_access_are_distinct(self):
        title = "破碎机备件采购公告"
        self.assertEqual(
            notice_rejection(
                title,
                "文件购买截止时间：2026-10-01；报名截止时间：2026-10-02",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            None,
        )
        self.assertEqual(
            notice_rejection(
                title,
                "报价截止时间：2026-10-16 11:59",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            "submission_expired",
        )
        self.assertEqual(
            notice_rejection(
                title,
                "响应(投标/报价)文件递交截止时间：2026-10-15",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            "submission_expired",
        )
        self.assertEqual(
            notice_rejection(
                title,
                "投标截止时间：2026年10月15日09时00分。投标文件应在投标截止时间前提交。",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            "submission_expired",
        )
        self.assertEqual(
            notice_rejection(
                title,
                "递交投标文件截止时间：202\n6\n年\n10\n月\n1\n5\n日\n0\n9\n:\n3\n0时（北京时间）",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            "submission_expired",
        )

    def test_multiple_deadlines_keep_notice_if_any_relevant_date_is_future(self):
        body = "投标截止时间：2026-10-15；报价截止时间：2026-10-18"
        self.assertIsNone(
            notice_rejection("破碎机采购公告", body, "procurement", "2026-10-16", now=NOW)
        )

    def test_category_and_closed_notice_filters(self):
        self.assertEqual(
            notice_rejection("矿业权新闻", "", "news", "2026-10-16", now=NOW),
            "non_procurement",
        )
        self.assertEqual(
            notice_rejection("破碎机采购中标公告", "", "procurement", "2026-10-16", now=NOW),
            "closed_notice",
        )
        # Generic body boilerplate mentioning a result must not turn a live
        # equipment title into an award record.
        self.assertIsNone(
            notice_rejection(
                "股份钒制品破碎机",
                "采购内容：破碎机。中标后按合同交付。",
                "procurement",
                "2026-10-16",
                now=NOW,
            )
        )

    def test_equipment_goods_examples_are_kept(self):
        examples = [
            "【西安研究院】YBK3矿用隔爆三相异步电机采购二标段项目公告",
            "【天地王坡】1000根DW42-300/110X(H)型矿用单体液压支柱.GHY-26676项目公告",
            "【北京华宇】白布煤矿选煤厂EPC总承包-带式输送机采购项目公告",
            "选矿分公司破碎机用主润滑油泵公开询比",
            "【武汉设计院】粉料存储及输送设备项目公告",
            "股份钒制品破碎机",
            "【唐山研究院】洗选装备产业基地智能化升级项目生产设备采购-机加工设备GHY-26677项目公告",
        ]
        for title in examples:
            with self.subTest(title=title):
                self.assertIsNone(
                    notice_rejection(title, "", "procurement", "2026-10-16", now=NOW)
                )

    def test_services_materials_and_background_terms_are_rejected(self):
        rejected = [
            "破碎机偏心套修复服务",
            "【西安研究院】某煤矿钻探劳务分包采购",
            "本钢设备公司-贾家堡铁矿新增中碎圆锥破碎机改造等工程需用钢材招标公告",
            "【唐山研究院】蒙古国煤矿选煤厂项目(钢结构采购)",
            "矿用软包电池制备材料采购",
            "破碎机衬板填料化工产品",
            "矿山设备租赁",
            "矿山设备监测技术服务",
        ]
        for title in rejected:
            with self.subTest(title=title):
                self.assertEqual(
                    notice_rejection(title, "", "procurement", "2026-10-16", now=NOW),
                    "non_equipment",
                )
        self.assertEqual(
            notice_rejection(
                "破碎机备件采购公告",
                "采购内容：破碎机偏心套修复服务。",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            "non_equipment",
        )
        self.assertIsNone(
            notice_rejection(
                "破碎机设备采购公告",
                "采购范围：破碎机供货、运输、安装、调试、技术服务及培训。",
                "procurement",
                "2026-10-16",
                now=NOW,
            )
        )
        self.assertEqual(
            notice_rejection(
                "破碎机技术服务",
                "采购范围：破碎机监测技术服务。",
                "procurement",
                "2026-10-16",
                now=NOW,
            ),
            "non_equipment",
        )
        body = "项目背景：既有破碎机设备；资格历史业绩：供应过破碎机。采购内容：钢材。"
        self.assertEqual(
            notice_rejection("钢材采购公告", body, "procurement", "2026-10-16", now=NOW),
            "non_equipment",
        )

    def test_generic_title_needs_explicit_scope_and_labels_are_complete(self):
        self.assertEqual(
            notice_rejection("(破碎机制造厂)设备采购公告", "项目背景：矿山有破碎机。", "procurement", "2026-10-16", now=NOW),
            "non_equipment",
        )
        self.assertEqual(
            notice_rejection("设备采购公告", "项目背景：矿山有破碎机。", "procurement", "2026-10-16", now=NOW),
            "non_equipment",
        )
        self.assertIsNone(
            notice_rejection(
                "设备采购公告",
                "采购内容：带式输送机及其备件。",
                "procurement",
                "2026-10-16",
                now=NOW,
            )
        )
        for title in (
            "煤矿井下压裂泵采购",
            "矿用光纤光栅调制解调仪",
            "矿用防爆柴油机尾气CO过滤器",
            "HJT井矸石充填项目装载机采购",
        ):
            with self.subTest(title=title):
                self.assertIsNone(
                    notice_rejection(title, "", "procurement", "2026-10-16", now=NOW)
                )
        self.assertTrue({
            "published_too_old", "published_in_future", "missing_published_at",
            "invalid_published_at", "non_procurement", "closed_notice",
            "non_equipment", "submission_expired",
        } <= set(REJECTION_LABELS))


if __name__ == "__main__":
    unittest.main()
