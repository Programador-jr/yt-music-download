# YouTube Music Downloader

Este projeto é um script Python que permite baixar músicas e playlists do YouTube e salvá-las em pastas específicas. As músicas individuais são salvas na pasta `music-download`, enquanto as playlists são salvas na pasta `playlist-download`.

## Funcionalidades

- **Baixar Músicas Individuais**: Baixe uma única música do YouTube e salve-a na pasta `music-download`.
- **Baixar Playlists**: Baixe uma playlist completa do YouTube e salve-a na pasta `playlist-download`.
- **Atualizar Metadados**: Gere e atualize automaticamente os metadados das músicas, incluindo título, artista, álbum, letras e capa.
- **Modo Verboso**: Controle o nível de detalhes da saída do script.

## Requisitos

- Python 3.6 ou superior
- `yt-dlp`
- `ffmpeg`
- `mutagen`
- `colorama`
- `Pillow`
- `requests`

## Instalação

1. Clone o repositório:

   ```bash
   git clone https://github.com/Programador-jr/yt-music-download.git
   cd youtube-music-downloader
   ```

2. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

3. Certifique-se de que `ffmpeg` esteja instalado e disponível no PATH do sistema.

## Uso

1. Execute o script:

   ```bash
   python youtube_music_downloader.py
   ```

2. Siga as instruções no menu para baixar músicas individuais ou playlists.

### Baixar uma Música Individual

1. Selecione a opção "Baixar uma única música do Youtube".
2. Insira o URL da música que deseja baixar.
3. A música será baixada e salva na pasta `music-download`.

### Baixar uma Playlist

1. Selecione a opção "Baixar playlist do Youtube".
2. Insira o URL da playlist que deseja baixar.
3. As músicas da playlist serão baixadas e salvas na pasta `playlist-download`.

## Estrutura de Pastas

- **music-download**: Pasta onde as músicas individuais são salvas.
- **playlist-download**: Pasta onde as playlists são salvas.

### Configuração

O script utiliza um arquivo de configuração `playlist_config.json` para armazenar as configurações da playlist. Este arquivo é criado automaticamente dentro da pasta da playlist baixada, dentro da pasta `playlist-download`.

## Inspiração

Este projeto foi inspirado no trabalho de [mitexleo](https://github.com/mitexleo)

## Exemplo de Uso

```bash
$ python youtube_music_downloader.py

YouTube Music Download v1.0
-----------------------------------------------------------
Este programa faz o download e atualiza automaticamente uma cópia local
de qualquer lista de reprodução do YouTube na forma de uma pasta de álbum de música
- As músicas são armazenadas em pastas de álbuns nomeadas pelo título da playlist
- Os álbuns existentes são atualizados com músicas novas ou ausentes
- Músicas faltantes são enviadas para o final do álbum [alternar na configuração]
- Os metadados da música são gerados automaticamente usando informações do vídeo
- Os metadados incluem título/artistas/álbum/letras/número de faixas
- As capas das músicas são criadas usando miniaturas de vídeo

[NOTA] Este programa e o ffmpeg podem estar bloqueados por antivírus.
Se você tiver algum problema, você pode tentar adicionar este programa
e sua pasta ffmpeg com as exclusões do seu antivírus.
-----------------------------------------------------------

1. Baixar playlist do Youtube
2. Baixar uma única música do Youtube
3. Atualizar playlist salva anteriormente
4. Atualizar uma única música na playlist
5. Modificar lista de reprodução salva anteriormente
6. Gerar configuração de lista de reprodução padrão
7. Alterar o diretório de trabalho atual
8. Sair

Selecione uma opção (1 de 8): 2

Por favor, insira o URL da música que você deseja baixar: https://www.youtube.com/watch?v=exemplo

O download foi concluído. Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.
```

## Contribuição

Contribuições são bem-vindas! Sinta-se à vontade para abrir issues e pull requests.

## Licença

Este projeto está licenciado sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.