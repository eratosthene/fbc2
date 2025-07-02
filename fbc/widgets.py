from flask_appbuilder.widgets import ListWidget


class DiscogsReleaseListWidget(ListWidget):
    template = "widgets/d_r_list.html"


class EbayListingListWidget(ListWidget):
    template = "widgets/e_l_list.html"


class EbayOrderListWidget(ListWidget):
    template = "widgets/e_o_list.html"
