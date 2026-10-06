# Сертификаты

`russian_trusted_root_ca.pem` — корневые сертификаты НУЦ Минцифры России (Russian Trusted Root CA и Sub CA).
Без них Python не доверяет серверам Сбера (SaluteSpeech: ngw.devices.sberbank.ru, smartspeech.sber.ru).
Взяты из открытого пакета `salute-speech` 2.0.0 (PyPI, лицензия MIT), файл `salute_speech/conf/russian.pem`.
Используются только для запросов к Сберу, на остальные сервисы не влияют.
