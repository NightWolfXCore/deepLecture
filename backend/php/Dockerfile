FROM php:8.3-fpm-bookworm
# Official image already includes curl, mbstring and fileinfo. mysqlnd and
# bundled PHP sources let PDO MySQL compile without downloading apt packages.
RUN docker-php-ext-install -j2 pdo_mysql
COPY backend/php/php.ini /usr/local/etc/php/conf.d/deeplecture.ini
WORKDIR /app/backend/php
COPY backend/php/ /app/backend/php/
RUN chmod -R a+rX /app/backend/php
RUN mkdir -p /data/videos /data/audio /data/imports /data/downloads \
    && chmod 0755 /data /data/audio /data/imports /data/downloads \
    && chmod 2775 /data/videos \
    && chown www-data:www-data /data/videos \
    && chown www-data:www-data /data/imports \
    && chown 1000:1000 /data/audio /data/downloads
CMD ["php-fpm"]
