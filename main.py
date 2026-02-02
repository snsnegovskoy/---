import arcade
import sqlite3
import hashlib
import os
from datetime import datetime

# ============================================================================
# КОНСТАНТЫ
# ============================================================================

SCREEN_W, SCREEN_H = 780, 450
GRAVITY, MOVE_SPEED, JUMP_SPEED = 1, 3, 15
TILE_SCALING = 1.68
MENU_WIDTH, MENU_HEIGHT = 800, 600


# ============================================================================
# БАЗА ДАННЫХ
# ============================================================================

class GameDatabase:
    def __init__(self, db_path="game_database.db"):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                level_id INTEGER NOT NULL,
                unlocked BOOLEAN DEFAULT 0,
                best_score INTEGER DEFAULT 0,
                stars INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, level_id)
            )
        ''')

        conn.commit()
        conn.close()

    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()

    def authenticate_user(self, username, password):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        password_hash = self.hash_password(password)

        cursor.execute(
            'SELECT id FROM users WHERE username = ? AND password_hash = ?',
            (username, password_hash)
        )
        user = cursor.fetchone()
        conn.close()

        if user:
            return True, user[0], "Вход выполнен успешно!"
        return False, None, "Неверное имя пользователя или пароль"

    def create_user(self, username, password):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # Проверяем существование пользователя
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            if cursor.fetchone():
                return False, None, "Пользователь с таким именем уже существует!"

            password_hash = self.hash_password(password)
            cursor.execute(
                'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                (username, password_hash)
            )
            user_id = cursor.lastrowid

            # Создаем прогресс для уровней
            for level_id in [1, 2]:
                unlocked = 1 if level_id == 1 else 0
                cursor.execute(
                    'INSERT INTO user_progress (user_id, level_id, unlocked) VALUES (?, ?, ?)',
                    (user_id, level_id, unlocked)
                )

            conn.commit()
            return True, user_id, "Пользователь создан успешно!"
        except Exception as e:
            conn.rollback()
            return False, None, f"Ошибка: {str(e)}"
        finally:
            conn.close()

    def get_user_progress(self, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT level_id, unlocked, best_score, stars 
            FROM user_progress 
            WHERE user_id = ? ORDER BY level_id
        ''', (user_id,))

        progress = {}
        for row in cursor.fetchall():
            level_id, unlocked, best_score, stars = row
            progress[level_id] = {
                'unlocked': bool(unlocked),
                'best_score': best_score,
                'stars': stars,
                'name': f"Уровень {level_id}"
            }

        conn.close()
        return progress

    def update_progress(self, user_id, level_id, score, deaths, time_taken=999):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Получаем текущий рекорд
        cursor.execute(
            'SELECT best_score, stars FROM user_progress WHERE user_id = ? AND level_id = ?',
            (user_id, level_id)
        )
        current = cursor.fetchone()
        current_score = current[0] if current else 0
        current_stars = current[1] if current else 0

        # Обновляем если новый рекорд
        new_score = max(score, current_score)

        # Вычисляем звезды
        stars = self.calculate_stars(score, deaths, time_taken)

        # Сохраняем лучший результат звезд
        stars = max(stars, current_stars)

        cursor.execute('''
            UPDATE user_progress 
            SET best_score = ?, stars = ?
            WHERE user_id = ? AND level_id = ?
        ''', (new_score, stars, user_id, level_id))

        # Разблокируем следующий уровень, если собрано достаточно очков
        if new_score >= 30 and level_id < 2:  # Только если есть следующий уровень
            next_level = level_id + 1
            cursor.execute(
                'SELECT COUNT(*) FROM user_progress WHERE user_id = ? AND level_id = ?',
                (user_id, next_level)
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    'INSERT INTO user_progress (user_id, level_id, unlocked) VALUES (?, ?, 1)',
                    (user_id, next_level)
                )
            else:
                cursor.execute(
                    'UPDATE user_progress SET unlocked = 1 WHERE user_id = ? AND level_id = ?',
                    (user_id, next_level)
                )

        conn.commit()
        conn.close()
        return stars

    def calculate_stars(self, score, deaths, time_taken):
        """Расчет звезд по стандартным правилам"""
        stars = 0

        # Звезда 1: собрано 50% предметов (макс 50 очков)
        if score >= 25:  # 50% от 50
            stars += 1
        # Звезда 2: собрано 75% предметов
        if score >= 38:  # 75% от 50
            stars += 1
        # Звезда 3: собраны все предметы
        if score >= 50:  # 100% от 50
            stars += 1
        # Звезда 4: прохождение без смертей
        if deaths == 0:
            stars += 1
        # Звезда 5: быстрое прохождение (< 60 секунд)
        if time_taken < 60:
            stars += 1

        return min(stars, 5)


# ============================================================================
# ОКНО РЕГИСТРАЦИИ/АВТОРИЗАЦИИ
# ============================================================================

class AuthWindow(arcade.Window):
    def __init__(self):
        super().__init__(MENU_WIDTH, MENU_HEIGHT, "Вход / Регистрация")
        arcade.set_background_color(arcade.color.DARK_SLATE_GRAY)

        self.db = GameDatabase()
        self.mode = "login"
        self.username = self.password = self.confirm_password = ""
        self.active_field = "username"
        self.message = ""
        self.message_color = arcade.color.GREEN

    def on_draw(self):
        self.clear()

        # Заголовок
        title = "ВХОД" if self.mode == "login" else "РЕГИСТРАЦИЯ"
        arcade.draw_text(title, MENU_WIDTH // 2, MENU_HEIGHT - 80,
                         arcade.color.WHITE, 40, anchor_x="center")

        # Поля ввода
        field_y_positions = {
            "username": MENU_HEIGHT // 2 + 50,
            "password": MENU_HEIGHT // 2 - 20,
            "confirm": MENU_HEIGHT // 2 - 90
        }

        # Имя пользователя
        username_y = field_y_positions["username"]
        field_color = arcade.color.LIGHT_BLUE if self.active_field == "username" else arcade.color.WHITE

        arcade.draw_text("Имя пользователя:", MENU_WIDTH // 2, username_y + 30,
                         arcade.color.WHITE, 20, anchor_x="center")
        arcade.draw_lrbt_rectangle_filled(
            MENU_WIDTH // 2 - 150, MENU_WIDTH // 2 + 150,
            username_y - 20, username_y + 20,
            field_color
        )
        arcade.draw_text(self.username or "Введите имя", MENU_WIDTH // 2, username_y,
                         arcade.color.BLACK, 20, anchor_x="center", anchor_y="center")

        # Пароль
        password_y = field_y_positions["password"]
        field_color = arcade.color.LIGHT_BLUE if self.active_field == "password" else arcade.color.WHITE

        arcade.draw_text("Пароль:", MENU_WIDTH // 2, password_y + 30,
                         arcade.color.WHITE, 20, anchor_x="center")
        arcade.draw_lrbt_rectangle_filled(
            MENU_WIDTH // 2 - 150, MENU_WIDTH // 2 + 150,
            password_y - 20, password_y + 20,
            field_color
        )
        hidden_password = "*" * len(self.password)
        arcade.draw_text(hidden_password or "Введите пароль", MENU_WIDTH // 2, password_y,
                         arcade.color.BLACK, 20, anchor_x="center", anchor_y="center")

        # Подтверждение пароля (только для регистрации)
        if self.mode == "register":
            confirm_y = field_y_positions["confirm"]
            field_color = arcade.color.LIGHT_BLUE if self.active_field == "confirm" else arcade.color.WHITE

            arcade.draw_text("Подтвердите пароль:", MENU_WIDTH // 2, confirm_y + 30,
                             arcade.color.WHITE, 20, anchor_x="center")
            arcade.draw_lrbt_rectangle_filled(
                MENU_WIDTH // 2 - 150, MENU_WIDTH // 2 + 150,
                confirm_y - 20, confirm_y + 20,
                field_color
            )
            hidden_confirm = "*" * len(self.confirm_password)
            arcade.draw_text(hidden_confirm or "Подтвердите пароль", MENU_WIDTH // 2, confirm_y,
                             arcade.color.BLACK, 20, anchor_x="center", anchor_y="center")

        # Кнопки
        if self.mode == "login":
            # Кнопка "Войти"
            login_y = MENU_HEIGHT // 2 - 160
            arcade.draw_lrbt_rectangle_filled(
                MENU_WIDTH // 2 - 150, MENU_WIDTH // 2 + 150,
                login_y - 25, login_y + 25,
                arcade.color.GREEN
            )
            arcade.draw_text("Войти", MENU_WIDTH // 2, login_y,
                             arcade.color.WHITE, 24, anchor_x="center", anchor_y="center")

            # Кнопка "Регистрация"
            register_y = MENU_HEIGHT // 2 - 220
            arcade.draw_lrbt_rectangle_filled(
                MENU_WIDTH // 2 - 150, MENU_WIDTH // 2 + 150,
                register_y - 25, register_y + 25,
                arcade.color.BLUE
            )
            arcade.draw_text("Нет аккаунта? Зарегистрироваться", MENU_WIDTH // 2, register_y,
                             arcade.color.WHITE, 18, anchor_x="center", anchor_y="center")

        else:  # register mode
            # Кнопка "Зарегистрироваться"
            register_y = MENU_HEIGHT // 2 - 160
            arcade.draw_lrbt_rectangle_filled(
                MENU_WIDTH // 2 - 150, MENU_WIDTH // 2 + 150,
                register_y - 25, register_y + 25,
                arcade.color.GREEN
            )
            arcade.draw_text("Зарегистрироваться", MENU_WIDTH // 2, register_y,
                             arcade.color.WHITE, 24, anchor_x="center", anchor_y="center")

            # Кнопка "Назад"
            back_y = MENU_HEIGHT // 2 - 220
            arcade.draw_lrbt_rectangle_filled(
                MENU_WIDTH // 2 - 150, MENU_WIDTH // 2 + 150,
                back_y - 25, back_y + 25,
                arcade.color.BLUE
            )
            arcade.draw_text("Назад ко входу", MENU_WIDTH // 2, back_y,
                             arcade.color.WHITE, 18, anchor_x="center", anchor_y="center")

        # Сообщение
        if self.message:
            arcade.draw_text(self.message, MENU_WIDTH // 2, 50,
                             self.message_color, 18, anchor_x="center")

    def on_mouse_press(self, x, y, button, modifiers):
        if button != arcade.MOUSE_BUTTON_LEFT:
            return

        # Проверяем клик по полям ввода
        field_y_positions = {
            "username": MENU_HEIGHT // 2 + 50,
            "password": MENU_HEIGHT // 2 - 20,
            "confirm": MENU_HEIGHT // 2 - 90
        }

        for field_name, field_y in field_y_positions.items():
            if self.mode == "login" and field_name == "confirm":
                continue

            if (MENU_WIDTH // 2 - 150 <= x <= MENU_WIDTH // 2 + 150 and
                    field_y - 20 <= y <= field_y + 20):
                self.active_field = field_name
                return

        # Проверяем клик по кнопкам
        if self.mode == "login":
            # Кнопка "Войти"
            login_y = MENU_HEIGHT // 2 - 160
            if (MENU_WIDTH // 2 - 150 <= x <= MENU_WIDTH // 2 + 150 and
                    login_y - 25 <= y <= login_y + 25):
                self.login()
                return

            # Кнопка "Регистрация"
            register_y = MENU_HEIGHT // 2 - 220
            if (MENU_WIDTH // 2 - 150 <= x <= MENU_WIDTH // 2 + 150 and
                    register_y - 25 <= y <= register_y + 25):
                self.mode = "register"
                self.message = ""
                self.confirm_password = ""
                self.active_field = "username"
                return

        else:  # register mode
            # Кнопка "Зарегистрироваться"
            register_y = MENU_HEIGHT // 2 - 160
            if (MENU_WIDTH // 2 - 150 <= x <= MENU_WIDTH // 2 + 150 and
                    register_y - 25 <= y <= register_y + 25):
                self.register()
                return

            # Кнопка "Назад"
            back_y = MENU_HEIGHT // 2 - 220
            if (MENU_WIDTH // 2 - 150 <= x <= MENU_WIDTH // 2 + 150 and
                    back_y - 25 <= y <= back_y + 25):
                self.mode = "login"
                self.message = ""
                self.confirm_password = ""
                self.active_field = "username"
                return

    def on_key_press(self, key, modifiers):
        if key == arcade.key.ESCAPE:
            self.close()
        elif key == arcade.key.TAB:
            # Переключение между полями
            if self.mode == "login":
                fields = ["username", "password"]
            else:
                fields = ["username", "password", "confirm"]

            if self.active_field in fields:
                idx = fields.index(self.active_field)
                self.active_field = fields[(idx + 1) % len(fields)]
            else:
                self.active_field = fields[0]
        elif key == arcade.key.ENTER:
            if self.mode == "login":
                self.login()
            else:
                self.register()
        elif key == arcade.key.BACKSPACE:
            if self.active_field == "username":
                self.username = self.username[:-1]
            elif self.active_field == "password":
                self.password = self.password[:-1]
            elif self.active_field == "confirm":
                self.confirm_password = self.confirm_password[:-1]
        elif 32 <= key <= 126:
            char = chr(key)
            if self.active_field == "username":
                self.username += char
            elif self.active_field == "password":
                self.password += char
            elif self.active_field == "confirm":
                self.confirm_password += char

    def login(self):
        if not self.username or not self.password:
            self.message = "Заполните все поля!"
            self.message_color = arcade.color.RED
            return

        success, user_id, message = self.db.authenticate_user(self.username, self.password)
        self.message = message
        self.message_color = arcade.color.GREEN if success else arcade.color.RED

        if success:
            self.close()
            level_menu = LevelMenu(user_id, self.db)
            level_menu.show()

    def register(self):
        if not all([self.username, self.password, self.confirm_password]):
            self.message = "Заполните все поля!"
            self.message_color = arcade.color.RED
            return

        if self.password != self.confirm_password:
            self.message = "Пароли не совпадают!"
            self.message_color = arcade.color.RED
            return

        if len(self.username) < 3:
            self.message = "Имя пользователя должно быть не менее 3 символов"
            self.message_color = arcade.color.RED
            return

        if len(self.password) < 4:
            self.message = "Пароль должен быть не менее 4 символов"
            self.message_color = arcade.color.RED
            return

        success, user_id, message = self.db.create_user(self.username, self.password)
        self.message = message
        self.message_color = arcade.color.GREEN if success else arcade.color.RED

        if success:
            self.mode = "login"
            self.password = self.confirm_password = ""
            self.active_field = "username"


# ============================================================================
# МЕНЮ ВЫБОРА УРОВНЯ
# ============================================================================

class LevelMenu(arcade.Window):
    def __init__(self, user_id, db):
        super().__init__(MENU_WIDTH, MENU_HEIGHT, "Выбор уровня")
        arcade.set_background_color(arcade.color.SKY_BLUE)

        self.user_id = user_id
        self.db = db
        self.progress = self.db.get_user_progress(user_id)
        self.hovered_level = None

    def show(self):
        """Показать меню"""
        self.progress = self.db.get_user_progress(self.user_id)
        arcade.run()

    def on_draw(self):
        self.clear()

        arcade.draw_text("ВЫБОР УРОВНЯ", MENU_WIDTH // 2, MENU_HEIGHT - 50,
                         arcade.color.NAVY_BLUE, 36, anchor_x="center")

        # Отображаем уровни
        for level_id in [1, 2]:
            level_info = self.progress.get(level_id, {
                'unlocked': level_id == 1,
                'best_score': 0,
                'stars': 0,
                'name': f"Уровень {level_id}"
            })

            x = MENU_WIDTH // 3 if level_id == 1 else 2 * MENU_WIDTH // 3
            y = MENU_HEIGHT // 2

            # Фон уровня
            color = arcade.color.LIGHT_GRAY
            if not level_info['unlocked']:
                color = arcade.color.DARK_GRAY
            elif self.hovered_level == level_id:
                color = arcade.color.LIGHT_BLUE

            arcade.draw_lrbt_rectangle_filled(
                x - 90, x + 90,
                y - 50, y + 50,
                color
            )
            arcade.draw_lrbt_rectangle_outline(
                x - 90, x + 90,
                y - 50, y + 50,
                arcade.color.BLACK, 2
            )

            # Название
            text_color = arcade.color.BLACK if level_info['unlocked'] else arcade.color.GRAY
            arcade.draw_text(level_info['name'], x, y + 20,
                             text_color, 20, anchor_x="center", anchor_y="center")

            # Статистика
            if level_info['best_score'] > 0:
                arcade.draw_text(f"Очки: {level_info['best_score']}", x, y - 10,
                                 arcade.color.DARK_GREEN, 14, anchor_x="center", anchor_y="center")

            # Звезды
            if level_info['stars'] > 0:
                for i in range(5):
                    star_x = x - 40 + i * 20
                    star_y = y - 30
                    if i < level_info['stars']:
                        arcade.draw_circle_filled(star_x, star_y, 8, arcade.color.GOLD)
                    else:
                        arcade.draw_circle_outline(star_x, star_y, 8, arcade.color.GRAY, 1)

            # Замок для заблокированных
            if not level_info['unlocked']:
                arcade.draw_text("🔒", x, y - 40,
                                 arcade.color.BLACK, 24, anchor_x="center", anchor_y="center")

        arcade.draw_text("Нажмите на уровень для начала игры", MENU_WIDTH // 2, 100,
                         arcade.color.DARK_GRAY, 16, anchor_x="center")
        arcade.draw_text("ESC - выход в главное меню", MENU_WIDTH // 2, 70,
                         arcade.color.DARK_GRAY, 14, anchor_x="center")

    def on_mouse_motion(self, x, y, dx, dy):
        self.hovered_level = None
        for level_id in [1, 2]:
            level_x = MENU_WIDTH // 3 if level_id == 1 else 2 * MENU_WIDTH // 3
            level_y = MENU_HEIGHT // 2

            if (level_x - 90 <= x <= level_x + 90 and
                    level_y - 50 <= y <= level_y + 50):

                level_info = self.progress.get(level_id, {'unlocked': level_id == 1})
                if level_info['unlocked']:
                    self.hovered_level = level_id
                break

    def on_mouse_press(self, x, y, button, modifiers):
        if button != arcade.MOUSE_BUTTON_LEFT:
            return

        for level_id in [1, 2]:
            level_x = MENU_WIDTH // 3 if level_id == 1 else 2 * MENU_WIDTH // 3
            level_y = MENU_HEIGHT // 2

            if (level_x - 90 <= x <= level_x + 90 and
                    level_y - 50 <= y <= level_y + 50):

                level_info = self.progress.get(level_id, {'unlocked': level_id == 1})
                if level_info['unlocked']:
                    self.close()
                    game_window = GameWindow(level_id, self.user_id, self.db)
                    game_window.run()
                    # После завершения игры обновляем прогресс и показываем меню
                    self.progress = self.db.get_user_progress(self.user_id)
                    self.show_view()
                    break

    def on_key_press(self, key, modifiers):
        if key == arcade.key.ESCAPE:
            self.close()
            # Возвращаемся к окну авторизации
            auth_window = AuthWindow()
            auth_window.run()

    def show_view(self):
        """Показать это окно снова"""
        self.__init__(self.user_id, self.db)
        arcade.run()


# ============================================================================
# ИГРОВОЕ ОКНО
# ============================================================================

class GameWindow(arcade.Window):
    def __init__(self, level_id, user_id, db):
        super().__init__(SCREEN_W, SCREEN_H, f"Уровень {level_id}")
        arcade.set_background_color(arcade.color.SKY_BLUE)

        self.level_id = level_id
        self.user_id = user_id
        self.db = db

        self.score = 0
        self.max_score = 0  # Максимально возможный счет для этого уровня
        self.health = 100
        self.deaths = 0
        self.start_time = datetime.now()
        self.has_key = False
        self.level_complete = False

        # Списки спрайтов
        self.player_list = arcade.SpriteList()
        self.walls = None
        self.collectibles = None
        self.exit_list = None
        self.damage_list = None
        self.ladder_list = None
        self.batut_list = None
        self.characters_list = None  # Слой персонажей

        # Для восстановления предметов при смерти
        self.original_collectibles_data = []  # Храним данные оригинальных предметов

        # Игрок
        self.player = None

        # Физический движок
        self.physics_engine = None

        # Управление
        self.left = self.right = self.up = self.down = False
        self.jump_pressed = False
        self.on_ladder = False
        self.can_jump = False

        # Таймер неуязвимости после получения урона
        self.invincible_timer = 0
        self.INVINCIBLE_TIME = 1.0

        # Для прыжка с лестницы
        self.ladder_jump_cooldown = 0

        self.setup_level()

    def setup_level(self):
        """Загрузка уровня"""
        # Пути к файлам уровней
        level_files = {
            1: r"C:\Users\NNSneg\Desktop\Проект.tmx",
            2: r"C:\Users\NNSneg\Desktop\Проект2.tmx"
        }

        file_path = level_files.get(self.level_id)

        if file_path and os.path.exists(file_path):
            try:
                # Загружаем карту Tiled
                layer_options = {
                    "collision": {"use_spatial_hash": True, "scaling": TILE_SCALING},
                    "collect": {"use_spatial_hash": True, "scaling": TILE_SCALING},
                    "exit": {"use_spatial_hash": True, "scaling": TILE_SCALING},
                    "damage": {"use_spatial_hash": True, "scaling": TILE_SCALING},
                    "ladder": {"use_spatial_hash": True, "scaling": TILE_SCALING},
                    "batut": {"use_spatial_hash": True, "scaling": TILE_SCALING},
                    "characters": {"use_spatial_hash": True, "scaling": TILE_SCALING}
                }

                self.tile_map = arcade.load_tilemap(
                    file_path,
                    scaling=TILE_SCALING,
                    layer_options=layer_options
                )

                # Получаем слои
                self.walls = self.tile_map.sprite_lists.get("collision") or arcade.SpriteList()
                self.collectibles = self.tile_map.sprite_lists.get("collect") or arcade.SpriteList()
                self.exit_list = self.tile_map.sprite_lists.get("exit") or arcade.SpriteList()
                self.damage_list = self.tile_map.sprite_lists.get("damage") or arcade.SpriteList()
                self.ladder_list = self.tile_map.sprite_lists.get("ladder") or arcade.SpriteList()
                self.batut_list = self.tile_map.sprite_lists.get("batut") or arcade.SpriteList()
                self.characters_list = self.tile_map.sprite_lists.get("characters") or arcade.SpriteList()

                # Сохраняем данные оригинальных предметов для восстановления
                self.original_collectibles_data = []
                if self.collectibles:
                    for item in self.collectibles:
                        # Сохраняем основные свойства предмета
                        item_data = {
                            'center_x': item.center_x,
                            'center_y': item.center_y,
                            'scale': item.scale,
                            'width': item.width,
                            'height': item.height,
                        }

                        # Проверяем тип спрайта
                        if hasattr(item, 'texture') and item.texture:
                            # Это спрайт с текстурой
                            item_data['type'] = 'textured'
                            # Сохраняем информацию о текстуре
                            if hasattr(item, 'texture') and item.texture:
                                item_data['texture'] = item.texture
                        elif hasattr(item, 'color'):
                            # Это цветной спрайт
                            item_data['type'] = 'colored'
                            item_data['color'] = item.color
                        else:
                            # По умолчанию считаем текстурированным
                            item_data['type'] = 'textured'

                        self.original_collectibles_data.append(item_data)

                # Сцена
                self.scene = arcade.Scene.from_tilemap(self.tile_map)

                # Подсчитываем максимально возможный счет
                if self.collectibles:
                    self.max_score = len(self.collectibles) * 10
                else:
                    self.max_score = 50  # Максимум 50 очков

            except Exception as e:
                print(f"Ошибка загрузки уровня: {e}")
                self.create_test_level()
        else:
            self.create_test_level()

        # Создаем игрока
        try:
            # Пробуем загрузить свою текстуру
            self.player = arcade.Sprite(r"C:\Users\NNSneg\Desktop\blue_slime_hero_24x24_strip5.png", scale=1.25)
        except:
            # Используем стандартную текстуру
            self.player = arcade.Sprite(":resources:images/animated_characters/female_person/femalePerson_idle.png",
                                        0.8)

        self.player.center_x, self.player.center_y = 100, 200
        self.player_list.append(self.player)

        # Физический движок для игрока
        if self.walls:
            self.physics_engine = arcade.PhysicsEnginePlatformer(
                self.player, self.walls, gravity_constant=GRAVITY,
                ladders=self.ladder_list
            )

    def create_test_level(self):
        """Создание уровня"""
        # Инициализируем списки
        self.walls = arcade.SpriteList(use_spatial_hash=True)
        self.collectibles = arcade.SpriteList()
        self.exit_list = arcade.SpriteList()
        self.damage_list = arcade.SpriteList()
        self.ladder_list = arcade.SpriteList()
        self.batut_list = arcade.SpriteList()
        self.characters_list = arcade.SpriteList()

        # Базовые платформы
        for x in range(0, 800, 64):
            wall = arcade.SpriteSolidColor(64, 64, arcade.color.GREEN)
            wall.center_x = x
            wall.center_y = 32
            self.walls.append(wall)

        # Добавляем платформы для разнообразия
        platform = arcade.SpriteSolidColor(200, 32, arcade.color.GREEN)
        platform.center_x = 300
        platform.center_y = 150
        self.walls.append(platform)

        # Лестница для тестирования
        for y in range(50, 200, 32):
            ladder = arcade.SpriteSolidColor(32, 32, arcade.color.BLUE)
            ladder.center_x = 400
            ladder.center_y = y
            self.ladder_list.append(ladder)

        # Монетки (5 штук для 50 очков)
        for i in range(5):
            coin = arcade.Sprite(":resources:images/items/coinGold.png", 0.5)
            coin.center_x = 150 + i * 80
            coin.center_y = 200
            self.collectibles.append(coin)

        # Сохраняем данные оригинальных предметов
        self.original_collectibles_data = []
        for item in self.collectibles:
            item_data = {
                'type': 'textured',
                'center_x': item.center_x,
                'center_y': item.center_y,
                'scale': item.scale,
                'width': item.width,
                'height': item.height,
                'texture': item.texture
            }
            self.original_collectibles_data.append(item_data)

        # Выход
        exit_sprite = arcade.Sprite(":resources:images/tiles/lockYellow.png", 0.8)
        exit_sprite.center_x = 700
        exit_sprite.center_y = 200
        self.exit_list.append(exit_sprite)

        # Тестовый враг для обоих уровней
        enemy = arcade.SpriteSolidColor(32, 32, arcade.color.DARK_RED)
        enemy.center_x = 500
        enemy.center_y = 100
        self.characters_list.append(enemy)

        # Создаем сцену
        self.scene = arcade.Scene()
        self.scene.add_sprite_list("walls", sprite_list=self.walls)

        # Максимальный счет для тестового уровня
        self.max_score = 50  # 5 монеток * 10 очков

    def on_draw(self):
        self.clear()

        # Отрисовка мира
        if hasattr(self, 'scene') and self.scene:
            self.scene.draw()

        if self.collectibles:
            self.collectibles.draw()
        if self.exit_list:
            self.exit_list.draw()
        if self.damage_list:
            self.damage_list.draw()
        if self.ladder_list:
            self.ladder_list.draw()
        if self.batut_list:
            self.batut_list.draw()
        if self.characters_list:
            self.characters_list.draw()

        self.player_list.draw()

        # Интерфейс
        arcade.draw_lrbt_rectangle_filled(5, 250, SCREEN_H - 75, SCREEN_H - 5, (0, 0, 0, 150))

        arcade.draw_text(f"Уровень {self.level_id}", 10, SCREEN_H - 30, arcade.color.WHITE, 16)
        arcade.draw_text(f"Очки: {self.score}/{self.max_score}", 10, SCREEN_H - 50, arcade.color.WHITE, 16)

        # Здоровье с цветом в зависимости от количества
        health_color = arcade.color.GREEN
        if self.health <= 50:
            health_color = arcade.color.YELLOW
        if self.health <= 20:
            health_color = arcade.color.RED

        arcade.draw_text(f"Здоровье: {self.health}", 10, SCREEN_H - 70, health_color, 16)

        if self.has_key:
            arcade.draw_text("Ключ получен!", SCREEN_W - 150, SCREEN_H - 30, arcade.color.GOLD, 16)
            arcade.draw_text("Идите к выходу", SCREEN_W - 150, SCREEN_H - 50, arcade.color.YELLOW, 14)

        # Экран завершения
        if self.level_complete:
            arcade.draw_lrbt_rectangle_filled(0, SCREEN_W, 0, SCREEN_H, (0, 0, 0, 200))
            arcade.draw_text("УРОВЕНЬ ПРОЙДЕН!", SCREEN_W // 2, SCREEN_H // 2 + 50,
                             arcade.color.GOLD, 36, anchor_x="center")
            arcade.draw_text(f"Очки: {self.score}/{self.max_score}", SCREEN_W // 2, SCREEN_H // 2,
                             arcade.color.WHITE, 24, anchor_x="center")
            arcade.draw_text(f"Смерти: {self.deaths}", SCREEN_W // 2, SCREEN_H // 2 - 30,
                             arcade.color.WHITE, 24, anchor_x="center")

            # Рассчет времени
            time_taken = (datetime.now() - self.start_time).total_seconds()
            arcade.draw_text(f"Время: {time_taken:.1f}с", SCREEN_W // 2, SCREEN_H // 2 - 60,
                             arcade.color.WHITE, 20, anchor_x="center")

            arcade.draw_text("Нажмите ESC для выхода в меню", SCREEN_W // 2, SCREEN_H // 2 - 100,
                             arcade.color.YELLOW, 18, anchor_x="center")

    def on_key_press(self, key, modifiers):
        if key == arcade.key.ESCAPE:
            if self.level_complete:
                # Сохраняем прогресс с корректным расчетом звезд
                time_taken = (datetime.now() - self.start_time).total_seconds()
                self.db.update_progress(self.user_id, self.level_id, self.score, self.deaths, time_taken)

            # Закрываем окно игры
            self.close()
            return

        if self.level_complete:
            return

        if key in (arcade.key.LEFT, arcade.key.A):
            self.left = True
        elif key in (arcade.key.RIGHT, arcade.key.D):
            self.right = True
        elif key in (arcade.key.UP, arcade.key.W):
            self.up = True
        elif key in (arcade.key.DOWN, arcade.key.S):
            self.down = True
        elif key == arcade.key.SPACE:
            self.jump_pressed = True

    def on_key_release(self, key, modifiers):
        if key in (arcade.key.LEFT, arcade.key.A):
            self.left = False
        elif key in (arcade.key.RIGHT, arcade.key.D):
            self.right = False
        elif key in (arcade.key.UP, arcade.key.W):
            self.up = False
        elif key in (arcade.key.DOWN, arcade.key.S):
            self.down = False
        elif key == arcade.key.SPACE:
            self.jump_pressed = False

    def on_update(self, delta_time):
        if self.level_complete:
            return

        # Обновление таймеров
        if self.invincible_timer > 0:
            self.invincible_timer -= delta_time
            if self.invincible_timer <= 0:
                self.player.alpha = 255

        if self.ladder_jump_cooldown > 0:
            self.ladder_jump_cooldown -= delta_time

        # Управление движением
        move_x = 0
        if self.left and not self.right:
            move_x = -MOVE_SPEED
        elif self.right and not self.left:
            move_x = MOVE_SPEED

        self.player.change_x = move_x

        # Проверка на лестнице
        self.on_ladder = False
        if self.ladder_list:
            ladder_collisions = arcade.check_for_collision_with_list(self.player, self.ladder_list)
            self.on_ladder = len(ladder_collisions) > 0

        # Управление на лестнице
        if self.on_ladder:
            # Отключаем гравитацию на лестнице
            if self.physics_engine:
                self.physics_engine.gravity_constant = 0

            # Вертикальное движение на лестнице
            if self.up:
                self.player.change_y = MOVE_SPEED
            elif self.down:
                self.player.change_y = -MOVE_SPEED
            else:
                self.player.change_y = 0

            # Прыжок с лестницы
            if self.jump_pressed and self.ladder_jump_cooldown <= 0:
                self.player.change_y = JUMP_SPEED
                self.on_ladder = False
                self.ladder_jump_cooldown = 0.3

                # Даем горизонтальный импульс
                if self.left:
                    self.player.change_x = -MOVE_SPEED * 1.5
                elif self.right:
                    self.player.change_x = MOVE_SPEED * 1.5
        else:
            # Включаем гравитацию вне лестницы
            if self.physics_engine:
                self.physics_engine.gravity_constant = GRAVITY

            # Проверяем, стоит ли игрок на земле
            on_ground = False
            if self.physics_engine:
                on_ground = self.physics_engine.can_jump()

            # Обычный прычок с земли
            if self.jump_pressed and on_ground:
                self.player.change_y = JUMP_SPEED
                self.jump_pressed = False

        # Батут
        if self.batut_list:
            batut_hit = arcade.check_for_collision_with_list(self.player, self.batut_list)
            for batut in batut_hit:
                if self.player.change_y < 0:
                    self.player.change_y = 20
                    self.jump_pressed = False

        # Обновляем физику
        if self.physics_engine:
            self.physics_engine.update()

        # Сбор предметов
        if self.collectibles:
            collected = arcade.check_for_collision_with_list(self.player, self.collectibles)
            for item in collected:
                item.remove_from_sprite_lists()
                self.score += 10

                if self.score >= 50:
                    self.has_key = True

        # Проверка повреждений от опасных объектов
        self.check_damage()

        # Проверка выхода
        if self.has_key and self.exit_list:
            exit_hit = arcade.check_for_collision_with_list(self.player, self.exit_list)
            if exit_hit:
                self.level_complete = True

        # Смерть от падения
        if self.player.center_y < -100:
            self.player_die()

    def check_damage(self):
        """Проверка столкновений с опасными объектами"""
        # Если игрок неуязвим - пропускаем проверку
        if self.invincible_timer > 0:
            return

        # Проверка damage слоя
        if self.damage_list:
            damage_hit = arcade.check_for_collision_with_list(self.player, self.damage_list)
            if damage_hit:
                self.take_damage(20)

        # Проверка врагов
        if self.characters_list:
            # Проверяем столкновение с каждым врагом отдельно
            for enemy in self.characters_list:
                # Используем простую проверку расстояния
                distance_x = abs(self.player.center_x - enemy.center_x)
                distance_y = abs(self.player.center_y - enemy.center_y)

                # Если расстояние достаточно мало
                if distance_x < (self.player.width / 2 + enemy.width / 2) and distance_y < (
                        self.player.height / 2 + enemy.height / 2):
                    self.take_damage(25)
                    break  # Чтобы не наносить урон несколько раз за один кадр

    def take_damage(self, amount):
        """Нанесение урона игроку"""
        self.health -= amount

        if self.health <= 0:
            self.player_die()
        else:
            # Активируем неуязвимость
            self.invincible_timer = self.INVINCIBLE_TIME
            self.player.alpha = 128  # Полупрозрачность
            self.player.change_y = 8  # Отскок

            # Отбрасывание
            if self.player.center_x < SCREEN_W // 2:
                self.player.change_x = 5  # Вправо
            else:
                self.player.change_x = -5  # Влево

    def player_die(self):
        """Смерть игрока - восстанавливаем все предметы"""
        self.deaths += 1
        self.health = 100

        # Восстанавливаем здоровье игрока
        self.player.center_x, self.player.center_y = 100, 200
        self.player.change_x = self.player.change_y = 0
        self.player.alpha = 255

        # Сбрасываем ключ
        self.has_key = False

        # Сбрасываем таймеры
        self.invincible_timer = 0
        self.on_ladder = False
        self.ladder_jump_cooldown = 0

        self.restore_collectibles()

        # Сбрасываем счет (предметы восстановлены, нужно собрать заново)
        self.score = 0

    def restore_collectibles(self):
        """Восстановление всех предметов из слоя collect"""
        # Очищаем текущий список предметов
        if self.collectibles:
            self.collectibles.clear()

        # Создаем новый список предметов
        self.collectibles = arcade.SpriteList(use_spatial_hash=True)

        # Восстанавливаем предметы из сохраненных данных
        for item_data in self.original_collectibles_data:
            try:
                if item_data['type'] == 'textured' and 'texture' in item_data:
                    # Восстанавливаем текстурированный спрайт
                    # Создаем спрайт с той же текстурой
                    new_item = arcade.Sprite()
                    new_item.texture = item_data['texture']
                    new_item.scale = item_data['scale']
                    new_item.width = item_data['width']
                    new_item.height = item_data['height']
                    new_item.center_x = item_data['center_x']
                    new_item.center_y = item_data['center_y']
                elif item_data['type'] == 'colored' and 'color' in item_data:
                    # Восстанавливаем цветной спрайт
                    new_item = arcade.SpriteSolidColor(
                        item_data['width'], item_data['height'], item_data['color']
                    )
                    new_item.scale = item_data['scale']
                    new_item.center_x = item_data['center_x']
                    new_item.center_y = item_data['center_y']
                else:
                    # Если не удалось определить тип, создаем простой спрайт
                    new_item = arcade.SpriteSolidColor(32, 32, arcade.color.YELLOW)
                    new_item.center_x = item_data['center_x']
                    new_item.center_y = item_data['center_y']

                # Добавляем в список
                self.collectibles.append(new_item)

            except Exception as e:
                print(f"Ошибка восстановления предмета: {e}")
                # Создаем простой предмет как запасной вариант
                try:
                    fallback_item = arcade.SpriteSolidColor(32, 32, arcade.color.YELLOW)
                    fallback_item.center_x = item_data.get('center_x', 100)
                    fallback_item.center_y = item_data.get('center_y', 100)
                    self.collectibles.append(fallback_item)
                except:
                    continue


# ============================================================================
# ЗАПУСК ИГРЫ
# ============================================================================

def main():
    """Главная функция запуска игры"""
    auth_window = AuthWindow()
    auth_window.run()


if __name__ == "__main__":
    main()