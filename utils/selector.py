class CssSelector:

    def findURLCSS(link):
            domain_mappings = {
                "m.bixiange.me": "#mycontent ::text",
                "bixiange": "p ::text",
                "bixiang": "p ::text",
                "sj.uukanshu": "#read-page ::text",
                "t.uukanshu": "#read-page ::text",
                "uukanshu.cc": ".bbb.font-normal.readcotent ::text",
                "uukanshu": "#contentbox ::text",
                "trxs.me": ".read_chapterDetail ::text",
                "trxs.cc": ".read_chapterDetail ::text",
                "qbtr": ".read_chapterDetail ::text",
                "tongrenquan": ".read_chapterDetail ::text",
                "biqugeabc": ".text_row_txt >p ::text",
                # "uuks": "div#contentbox > p ::text",
                "jpxs": ".read_chapterDetail p ::text",
                "powanjuan": ".content p::text",
                "ffxs": ".content p::text",
                "sjks": ".content p::text",
                "txt520": "h1 ::text",
                "69shu": ".txtnav ::text",
                "ptwxz": "* ::text",
                "shu05": "#htmlContent ::text",
                "readwn": ".chapter-content ::text",
                "novelmt.com": ".chapter-content ::text",
                "wuxiax.com": ".chapter-content ::text",
                "fannovels.com": ".chapter-content ::text",
                "novelmtl.com": ".chapter-content ::text",
                "www.wuxiap.com": ".chapter-content ::text",
                "www.wuxiau.com": ".chapter-content ::text",
                "wuxiabee": ".chapter-content ::text",
                "novelsemperor": "* ::text",
                "novelsknight.com": "* ::text",
                "www.vbiquge.co": "#rtext ::text",
                "www.wfxs.com.tw": "#rtext ::text",
                "2015txt.com": "#cont-body ::text",
                "4ksw.com": "div.panel-body.content-body.content-ext ::text",
                "novelfull.com": "#chapter-content ::text",
                "novelroom.net": "div.reading-content ::text",
                "readlightnovel": "#growfoodsmart ::text",
                "m.qidian": "#chapterContent ::text",
                "m.bqg28.cc": "#chapterContent ::text",
                # "read.qidian": "p ::text",
                # "book.qidian": "p ::text",
                # "www.qidian": "p ::text",
                "www.youyoukanshu.com": "#content > div.content ::text",
                "www.piaoyuxuan.com": "#content > div.content ::text",
                "www.dongliuxiaoshuo.com": "#content > div.content ::text",
                "fanqienovel.com": "div.muye-reader-content.noselect ::text",
                "m.shubaow.net": "#nr1 ::text",
                "m.longteng788.com/": "#nr1 ::text",
                "m.xklxsw.com": "#nr1 ::text",
                "m.630shu.net": "#nr1 ::text",
                "m.ddxs.com": "#nr1 ::text",
                "www.xindingdianxsw.com/": "p ::text",
                "m.akshu8.com": "#content ::text",
                "soruncg.com": "#content ::text",
                "www.630shu.net": "#content ::text",
                "www.yifan.net": "#content ::text",
                "www.soxscc.net": "#content ::text",
                "feixs.com": "#content ::text",
                "www.tsxsw.net": "#content ::text",
                "www.ttshu8.com": "#content ::text",
                "www.szhhlt.com": "#content ::text",
                "www.bqg789.net": "#content ::text",
                "www.9xzw.com": "#content ::text",
                "www.28zww.cc": "#content ::text",
                "www.wnmtl.org": "#reader > div > div.chapter-container ::text",
                "m.yifan.net": "#chaptercontent ::text",
                "www.qcxxs.com": "body > div.container > div.row.row-detail > div > div ::text",
                "m.soxscc.net": "#chapter > div.content ::text",
                "m.ttshu8.com": "#chapter > div.content ::text",
                "metruyencv.com": "#article ::text",
                "www.gonet.cc": "body > div.container > div.row.row-detail > div > div ::text",
                "www.ops8.com": "#BookText ::text",
                "m.77z5.com": "#chaptercontent ::text",
                "m.lw52.com": "#chaptercontent ::text",
                "mtl-novel.net": "#chr-content ::text",
                "novelnext.com": "#chr-content ::text",
                "novelbin.net": "#chr-content ::text",
                "ncode.syosetu.com": "#novel_honbun ::text",
                "m.75zw.com/": "#chapter > div.a75zwcom_i63c9e1d ::text",
                "www.webnovelpub.com": "#chapter-container ::text",
                "m.tsxsw.net": "#nr ::text",
                "www.4ksw.com/": "div.panel-body.content-body.content-ext ::text",
                "tw.ixdzs.com": "#page > article ::text",
                "www.shulinw.com/": "#htmlContent ::text",
                "ranobes.com": "div.block.story.shortstory ::text",
                "m.bqg789.net": "#novelcontent ::text",
                "boxnovel.com": "div.cha-content ::text",
                "bonnovel.com": "div.cha-content ::text",
                "www.asxs.com": "#contents ::text",
                "www.72xsw.net": "div.box_box ::text",
                "www.31dv.com": "#booktxt ::text",
                "1stkissnovel.org": "div.entry-content > div > div > div > div.text-left ::text",
                # "www.libraryofakasha.com": "div.chapter-content ::text",
                # "sangtacviet.vip": ".contentbox ::text",
                "www.novelcool.com": "p ::text",
                "novelcool.org/": "p ::text",
                "m.sywx8.com": "#nr ::text",
                "www.tadu.com": "#partContent ::text",
                "www.biquge.bz/": "#content ::text",
                "www.yuyouku.com": "#txtContent ::text",
                "m.yuyouku.com": "#content ::text",
                "m.biquzw789.net": "#novelcontent ::text",
                "www.biquzw789.net": "#content ::text",
                "pandamtl.com/": "div.bixbox.episodedl > div > div.epcontent.entry-content ::text",
                "www.bookbl.com": "#content > div.content ::text",
                "www.60ks.cc": "#content ::text",
                "m.qbxsw.com": "#chapter > div.content ::text",
                "www.qbxsw.com": "#content ::text",
                "www.14sw.com": "#content ::text",
                "m.14sw.com": "#ChapterView ::text",
                "noblemtl.com": "div.epcontent.entry-content ::text",
                "www.piaotia.com": "#content ::text"
            }
            for domain, css_selector in domain_mappings.items():
                if domain in link:
                    return css_selector
            return "* ::text"

    def findchptitlecss(link):
        domain_mappings = {
            "trxs.me": [".infos>h1:first-child", ""],
            "trxs.cc": [".infos>h1:first-child", ""],
            "tongrenquan": [".infos>h1:first-child", ""],
            "qbtr": [".infos>h1:first-child", ""],
            "jpxs": [".infos>h1:first-child", ""],
            "ffxs8.com": ["body > div.detail.clearfix > div > div.info-desc > div.desc-detail > h1", ""],
            "txt520": ["title", ""],
            "ffxs": ["title", ""],
            "bixiang": [".desc>h1", ""],
            "powanjuan": [".desc>h1", ""],
            "sjks": [".box-artic>h1", ""],
            "sj.uukanshu": [".bookname", "#divContent >h3 ::text"],
            "t.uukanshu": [".bookname", "#divContent >h3 ::text"],
            "uukanshu.cc": [".booktitle", "h1 ::text"],
            "uuks": [".jieshao_content>h1", "h1#timu ::text"],
            "uukanshu": ["title", "h1#timu ::text"],
            "69shu": [".bread>a:nth-of-type(3)", "title ::text"],
            "m.qidian": ["#header > h1", ""],
            "m.soxscc.net": ["title", "#chapter > h1 ::text"],
            "m.ttshu8.com": ["title", "#chapter > h1 ::text"],
            "www.soxscc.net": ["#info > h1", "body > div.content_read > div > div.bookname > h1 ::text"],
            "www.bqg789.net": ["#info > h1", "body > div.content_read > div > div.bookname > h1 ::text"],
            "www.ops8.com": ["div.book-title", "#BookCon > h1"],
            "feixs.com": ["title", "#main > div.bookinfo.m10.clearfix > div.info > p.chaptertitle ::text"],
            "m.tsxsw.net": ["title", "h2 ::text"],
            "www.tsxsw.net": ["div.articleinfo > div.r > div.l2 > div > h1", "title ::text"],
            "tw.ixdzs.com": ["h1", "title ::text"],
            "www.asxs.com": ["#a_main > div.bdsub > dl > dd.info > div.book > div.btitle > h1",
                             "#amain > dl > dd:nth-child(2) > h1 ::text"],
            "www.72xsw.net": ["div.title > h1", "div > h1 ::text"],
            "www.9xzw.com": ["#info > div > h1", "div.bookname > h1 ::text"],
            "m.ddxs.com": ["body > div.header > h2 > font > font", "#nr_title ::text"],
            "www.31dv.com": ["#info > h1", "#wrapper > article > h1 ::text"],
            "www.biquge.bz/": ["#info > h1", "#wrapper > div.content_read > div > div.bookname > h1 ::text"],
            "www.yuyouku.com": ["#btop-info > div.container > article > div > ul > li:nth-child(1) > h1", "#h1 > h1 ::text"],
            "m.yuyouku.com": ["#btop-info > div.body > div > section > div.novel-cover > dl > dt", "#content > h1 ::text"],
            "m.biquzw789.net": ["body > div.main > div.catalog > div.catalog1 > h1", "#novelbody > div.nr_function > h1 ::text"],
            "www.biquzw789.net": ["#info", "#wrapper > div.content_read > div > div.bookname > h1 ::text"],
            "www.bookbl.com": ["h1", "#content > h1 ::text"],
            "www.60ks.cc": ["#bookinfo > div.bookright > div.booktitle > h1", "#center > div.title > h1 ::text"],
            "m.60ks.cc": ["#_52mb_h1 > a", "#nr_title ::text"],
            "m.qbxsw.com": ["#read > div.main > div.detail > p.name", "#chapter > h1 ::text"],
            "www.qbxsw.com": ["#info > h1", "body > div.content_read > div > div.bookname > h1 ::text"],
            "www.14sw.com": ["#info > h1", "#main > h1 ::text"],
            "m.14sw.com": ["body > div.container > div.mod.detail > div.bd.column-2 > div.right > h1", "body > div.container > h1 ::text"],
            "noblemtl.com": ["div.infox > h1", "div.epheader > h1 ::text"],
            "www.piaotia.com": ["#tl > a:nth-child(3)", "#content > h1 ::text"]
        }
        for domain, ret_array in domain_mappings.items():
            if domain in link:
                return ret_array

        return ["h1", "title ::text"]


    def find_next_selector(link):
        domain_mappings = {
            "readwn": ["#chapter-article > header > div > aside > nav > div.action-select > a.chnav.next",
                       "#chapter-article > header > div > div > h1 > a"],
            "novelfull.com": ["#next_chap", "#chapter > div > div > a"],
            "novelroom.net": ["#manga-reading-nav-foot > div > div.select-pagination > div > div.nav-next > a",
                              "#manga-reading-nav-head > div > div.entry-header_wrap > div > div.c-breadcrumb > ol > "
                              "li:nth-child(2) > a"],
            "readlightnovel": [
                "#ch-page-container > div > div.col-lg-8.content2 > div > div:nth-child(6) > ul > li:nth-child(3) > a",
                "#ch-page-container > div > div.col-lg-8.content2 > div > div:nth-child(1) > div > div > h1"],
            "www.youyoukanshu.com": ["None", "#content > div.readtop > div.pull-left.hidden-lg > a > font"],
            "m.shubaow.net": ["#novelcontent > div.page_chapter > ul > li:nth-child(4) > a",
                              "#novelbody > div.head > div.nav_name > h1 > font > font"],
            "m.xindingdianxsw.com": ["#chapter > div:nth-child(9) > a:nth-child(3)",
                                     "#chapter > div.path > a:nth-child(2)"],
            "www.xindingdianxsw.com": ["#A3", "div.con_top > a:nth-child(3)"],
            "m.longteng788.com/": ["#pb_next", "#_52mb_h1"],
            "m.75zw.com/": ["#chapter > div.pager.z1 > a:nth-child(3)",
                            "#chapter > div.path > a:nth-child(3) > font > font"],
            "www.wnmtl.org": ["#nextBtn", "#navBookName"],
            "m.akshu8.com": ["#container > div > div > div.reader-main > div.section-opt.m-bottom-opt > a:nth-child(5)",
                             "title"],
            "m.77z5.com": ["#pt_next", "#top > span > font > font:nth-child(3)"],
            "m.yifan.net/": ["#pb_next", "#read > div.header > span.title"],
            "www.yifan.net": ["#book > div.content > div:nth-child(5) > ul > li:nth-child(3) > a", "title"],
            "m.630shu.net": ["#pb_next", "title"],
            "www.qcxxs.com": ["body > div.container > div.row.row-detail > div > div > div.read_btn > a:nth-child(4)",
                              "body > div.container > div.row.row-detail > div > h2 > a:nth-child(3)"],
            "www.gonet.cc": ["body > div.container > div.row.row-detail > div > div > div.read_btn > a:nth-child(4)",
                             "body > div.container > div.row.row-detail > div > h2 > a:nth-child(3) > font > font"],
            "mtl-novel.net": ["#next_chap", "#chapter > div > div > a"],
            "ranobes.com": ["#next", "#dle-speedbar > span > font:nth-child(2) > span:nth-child(4) > a > span"],
            "booktoki216.com": ["#goNextBtn", "title"],
            "boxnovel.com": ["None",
                             "#manga-reading-nav-head > div > div.entry-header_wrap > div > div.c-breadcrumb > ol > "
                             "li:nth-child(2) > a"],
            "www.szhhlt.com": ["None", "body > div.container > header > h1 > label > a"],
            "69shu": ["None", "title"],
            "www.69shuba.com": ["None", "title"],
            "novelbin.net": ["None", "#chapter > div > div > a"],
            "1stkissnovel.org": ["None", "#chapter-heading"],
            "sangtacviet.vip": ["None", "title"],
            "m.sywx8.com": ["None", "head > meta:nth-child(9)"],
            "novelmtl.com": ["#chapter-article > header > div > aside > nav > div.action-select > a.chnav.next",
                             "#chapter-article > header > div > div > h1 > a"],
            "www.wuxiabee.com": ["#chapter-article > header > div > aside > nav > div.action-select > a.chnav.next",
                                 "#chapter-article > header > div > div > h1 > a"],
            "www.wuxia": ["#chapter-article > header > div > aside > nav > div.action-select > a.chnav.next",
                          "#chapter-article > header > div > div > h1 > a"],
            "fannovels.com": ["#chapter-article > header > div > aside > nav > div.action-select > a.chnav.next",
                              "#chapter-article > header > div > div > h1 > a"],
            "www.dongliuxiaoshuo.com": ["None", "#content > div.readtop > div.pull-left.hidden-lg > a > font"],
            "soruncg.com": ["#container > div > div > div.reader-main > div.section-opt.m-bottom-opt > a:nth-child(5)",
                            "title"],
            "novelnext.com": ["#next_chap", "#chapter > div > div > a"],
            "bonnovel.com": ["None",
                             "#manga-reading-nav-head > div > div.entry-header_wrap > div > div.c-breadcrumb > ol > li:nth-child(2) > a"],
            "pandamtl.com/": ["#post-9256 > div.bixbox.episodedl > div > div.navimedia > div.left > div > div:nth-child(3) > a", "#post-9256 > div.bixbox.episodedl > div > div.epheader > h1"]
            # "www.novelcool.com": ["None",  "div.chapter-reading-section-list > div > div > h2"],
            # "m.bqg789.net": ["#nextpage", "#novelbody > div.head > div.nav_name > h1"],
        }
        for domain, ret_array in domain_mappings.items():
            if domain in link:
                return ret_array

        return [None, "title"]
