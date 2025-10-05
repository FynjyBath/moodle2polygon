# moodle2polygon

Утилита для переноса задач из Moodle CodeRunner в Polygon. Скрипт читает XML
экспорт Moodle, создаёт задачи в Polygon через API, загружает условие, тесты и
авторское решение, устанавливает чекер `std::cmp_long_long_sequence`, собирает
пакет и выводит список идентификаторов созданных задач.

## Подготовка

1. Скопируйте `polygon_config.example.ini` в `polygon_config.ini` и заполните
   ключ и секрет, полученные в настройках Polygon API. При необходимости можно
   изменить `api_url`.
2. Убедитесь, что у вас есть XML файл, экспортированный из Moodle в формате
   CodeRunner.

## Запуск

```bash
python moodle2polygon.py path/to/export.xml --config polygon_config.ini
```

Параметр `--config` необязателен (по умолчанию используется файл
`polygon_config.ini` в текущей директории).

После завершения работы скрипт выведет в стандартный вывод идентификаторы
созданных задач Polygon.