<?php
declare(strict_types=1);
namespace DeepLecture;

use PDO;
use PDOException;

final class Database
{
    private ?PDO $connection = null;

    public function pdo(): PDO
    {
        if ($this->connection === null) {
            try {
                $dsn = sprintf('mysql:host=%s;port=%s;dbname=%s;charset=utf8mb4', Config::env('DB_HOST', 'mysql'), Config::env('DB_PORT', '3306'), Config::env('DB_NAME', 'deeplecture'));
                $this->connection = new PDO($dsn, Config::env('DB_USER', 'deeplecture'), Config::env('DB_PASSWORD'), [
                    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                    PDO::ATTR_EMULATE_PREPARES => false,
                    PDO::ATTR_TIMEOUT => 3,
                ]);
                $this->connection->exec("SET time_zone = '+00:00'");
                $this->connection->exec('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ');
            } catch (PDOException) {
                throw new ApiError(503, 'DATABASE_UNAVAILABLE', 'База данных временно недоступна');
            }
        }
        return $this->connection;
    }

    public function transaction(callable $callback): mixed
    {
        $pdo = $this->pdo();
        for ($attempt = 0; ; $attempt++) {
            $pdo->beginTransaction();
            try {
                $result = $callback($pdo);
                $pdo->commit();
                return $result;
            } catch (\Throwable $e) {
                if ($pdo->inTransaction()) {
                    $pdo->rollBack();
                }
                if ($e instanceof PDOException && in_array((int) ($e->errorInfo[1] ?? 0), [1205, 1213], true) && $attempt < 2) {
                    usleep(random_int(10000, 50000));
                    continue;
                }
                throw $e;
            }
        }
    }

    public function query(string $sql, array $params = []): \PDOStatement
    {
        $statement = $this->pdo()->prepare($sql);
        $statement->execute($params);
        return $statement;
    }
}
