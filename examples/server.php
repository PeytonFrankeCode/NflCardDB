<?php
/**
 * The same idea in PHP, for shared hosting.
 *
 * The key lives in an environment variable, not in this file, so it does not
 * end up in version control or in a backup someone can read.
 *
 *   /api/price.php?player=CJ+Stroud&grader=PSA&grade=10
 */
declare(strict_types=1);

$api = getenv('NFLCARDDB_API');
$key = getenv('NFLCARDDB_KEY');

header('Content-Type: application/json');

if (!$api || !$key) {
    http_response_code(500);
    echo json_encode(['error' => 'API not configured']);
    exit;
}

$player = substr((string)($_GET['player'] ?? ''), 0, 60);
if ($player === '') {
    http_response_code(400);
    echo json_encode(['error' => 'player is required']);
    exit;
}

$query = ['player' => $player];
if (!empty($_GET['grader'])) $query['grader'] = substr((string)$_GET['grader'], 0, 8);
if (!empty($_GET['grade']))  $query['grade']  = (float)$_GET['grade'];

$url = $api . '/v1/prices?' . http_build_query($query);

$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT        => 10,
    CURLOPT_HTTPHEADER     => ['Authorization: Bearer ' . $key],
]);
$body   = curl_exec($ch);
$status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
curl_close($ch);

if ($body === false || $status >= 400) {
    // Do not pass the upstream error through: it can reveal quota state.
    http_response_code(502);
    echo json_encode(['error' => 'price lookup unavailable']);
    exit;
}

header('Cache-Control: public, max-age=300');
echo $body;
