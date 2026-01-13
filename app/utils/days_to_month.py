# app/utils/days_to_month.py

def days_to_str(days: int) -> str:
    """
    Преобразует количество дней в "календарную" строку (например, "1 месяц", "30 дней")
    и правильно склоняет слова.
    """
    if not isinstance(days, int) or days <= 0:
        return ""

    # Вспомогательная функция для склонений
    def get_plural(number, forms):
        if number % 10 == 1 and number % 100 != 11:
            return forms[0]
        elif 2 <= number % 10 <= 4 and (number % 100 < 10 or number % 100 >= 20):
            return forms[1]
        else:
            return forms[2]

    month_forms = ['месяц', 'месяца', 'месяцев']
    day_forms = ['день', 'дня', 'дней']

    # Среднее количество дней в месяце
    AVERAGE_DAYS_IN_MONTH = 365.25 / 12

    # Если дней меньше, чем в среднем месяце, то просто выводим дни
    if days < 29:  # Используем 29 как порог для простоты
        return f"{days} {get_plural(days, day_forms)}"

    months = round(days / AVERAGE_DAYS_IN_MONTH)

    # Если после округления получилось 0 месяцев, значит, это примерно 1 месяц
    if months == 0 and days >= 29:
        months = 1

    # Формируем итоговую строку
    if months > 0:
        return f"{months} {get_plural(months, month_forms)}"

    # На случай, если что-то пошло не так, вернем просто дни
    return f"{days} {get_plural(days, day_forms)}"
