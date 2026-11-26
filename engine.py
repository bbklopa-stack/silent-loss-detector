SECTORS = {
    "clothing": {
        "title": "متاجر الملابس",
        "expenses": [
            {"item": "إعلانات ممولة غير مستهدفة", "normal": 1200, "actual": 1200},
            {"item": "مرتجعات الشحن والاستبدال", "normal": 450, "actual": 450},
            {"item": "التغليف والملصقات", "normal": 300, "actual": 300},
        ],
        "sales": {"السبت": 1000, "الأحد": 1000, "الإثنين": 1000, "الثلاثاء": 1000, "الأربعاء": 1000, "الخميس": 1000},
    },
    "restaurants": {
        "title": "المطاعم والكافيهات",
        "expenses": [
            {"item": "هدر المواد الغذائية والتلف", "normal": 600, "actual": 600},
            {"item": "عمولات تطبيقات التوصيل", "normal": 800, "actual": 800},
            {"item": "الغاز والكهرباء التشغيلية", "normal": 500, "actual": 500},
        ],
        "sales": {"السبت": 1000, "الأحد": 1000, "الإثنين": 1000, "الثلاثاء": 1000, "الأربعاء": 1000, "الخميس": 1000},
    },
    "pharmacies": {
        "title": "الصيدليات",
        "expenses": [
            {"item": "أدوية منتهية الصلاحية", "normal": 350, "actual": 350},
            {"item": "شحن وتخزين مبرد", "normal": 400, "actual": 400},
            {"item": "مستلزمات تعبئة وأكياس", "normal": 150, "actual": 150},
        ],
        "sales": {"السبت": 1000, "الأحد": 1000, "الإثنين": 1000, "الثلاثاء": 1000, "الأربعاء": 1000, "الخميس": 1000},
    },
    "distribution": {
        "title": "شركات التوزيع",
        "expenses": [
            {"item": "وقود وصيانة أسطول النقل", "normal": 1500, "actual": 1500},
            {"item": "غرامات تأخير وتسليم ناقص", "normal": 400, "actual": 400},
            {"item": "لوازم المستودع والفواتير", "normal": 350, "actual": 350},
        ],
        "sales": {"السبت": 1000, "الأحد": 1000, "الإثنين": 1000, "الثلاثاء": 1000, "الأربعاء": 1000, "الخميس": 1000},
    },
}


def run_audit(sector_key="clothing", custom_products=None):
    sector = SECTORS.get(sector_key, SECTORS["clothing"])
    raw_products = custom_products if custom_products else []

    products = []
    losing_count = 0
    total_extra_added = 0.0
    total_loss_value = 0.0

    for p in raw_products:
        cost = round(float(p["cost"]), 2)
        extra = round(float(p["extra"]), 2)
        price = round(float(p["price"]), 2)
        real_cost = round(cost + extra, 2)
        net_profit = round(price - real_cost, 2)
        is_loss = net_profit < 0
        if is_loss:
            losing_count += 1
            total_loss_value += abs(net_profit)
        total_extra_added += extra

        products.append({
            "name": str(p["name"]),
            "cost": cost,
            "extra": extra,
            "real_cost": real_cost,
            "price": price,
            "net_profit": net_profit,
            "status": "خسارة" if is_loss else "ربح",
        })

    extra_per_item = round(total_extra_added / max(len(sector["expenses"]), 1), 2)
    loss_impact = round(total_loss_value * 10, 2)

    expenses = []
    total_leak = 0.0
    for idx, e in enumerate(sector["expenses"]):
        normal = round(float(e["normal"]), 2)
        dynamic_bump = extra_per_item + (loss_impact if idx == 0 else 0.0)
        actual = round(float(e["actual"]) + dynamic_bump, 2)
        diff = round(actual - normal, 2)
        is_abnormal = diff > normal * 0.5
        if is_abnormal:
            total_leak += max(0.0, diff)

        expenses.append({
            "item": e["item"],
            "normal": normal,
            "actual": actual,
            "diff": diff,
            "is_abnormal": is_abnormal,
        })

    sales = dict(sector["sales"])
    slump_days = []
    if sales and raw_products:
        avg_sales = sum(sales.values()) / len(sales)
        pressure_factor = min(0.5, (total_extra_added + total_loss_value) / 200)
        days_list = list(sales.keys())
        for i, day in enumerate(days_list):
            day_weight = 1.0 if i % 2 == 0 else 0.5
            adjusted_val = round(sales[day] * (1 - pressure_factor * day_weight), 2)
            sales[day] = adjusted_val
            ratio = (adjusted_val - avg_sales) / avg_sales
            if ratio <= -0.15:
                slump_days.append(f"{day} ({round(ratio * 100)}%)")

    return {
        "sector_key": sector_key,
        "sector_title": sector["title"],
        "total_expense_leak": round(total_leak, 2),
        "slump_days": slump_days or ["لا يوجد تراجع حاد"],
        "losing_products_count": losing_count,
        "expenses": expenses,
        "sales": sales,
        "products": products,
    }