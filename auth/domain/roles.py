from enum import Enum


class Role(str, Enum):
    EMPLOYEE = "Сотрудник"
    IT_DEPARTMENT = "IT отдел"
    PARTNER = "Партнер"
    ADMIN = "Администратор"
    MAIN_ADMIN = "Главный администратор"
    OFFICE_MANAGER = "Офис менеджер"


class TimeTrackingRole(str, Enum):


    USER = "user"
    MANAGER = "manager"
