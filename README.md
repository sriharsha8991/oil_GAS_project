# OIL AND GAS PROJECT


ingestion pipeline : 

PDF ingestion for well report documents : these are documents which has images of well, depths , graphs plots and tables .

pretty complex pdfs to handle all kinds of text images and tabular structure with layout awarness : 

Propose architecture and techstack : 

we are going to use Geminis capabilities and langraph orchestration setup 
has Gemini Tier 3 Capabilities 
The Pipeline: 
0. Do the extraction with PYMUPDF trigger a fall back when no extraction from page paddle ocr and the compare the content of each page if the extraction is properly done.
we were basically wanted to compare the extraction if any mismathes and  
1. we will get the pdf as input -> Do rasterization --> convert all the pdf pages into images
2. create a deque for passing this images into extraction phase for faster low latency pipeline and handling memory efficiently 
3. the agents pipeline will pickup the images on demand as the process moves on
4. this three agent extraction framwork posses three individual agents :
  - the first agent (identifier) recieves the image and content from the intial extraction for a given page : here it identifies what are the meaningful information to be extracted or missed from the image : 
  like text blocks, images , tables , so basically it identifies the layout and important things to be extracted from the image that adds value to the system. and then passes to the extraction agent where extraction of the identifed contents from the image happens. 
  - the extraction is purely responsible for extracting all the contents of the image and passes to the evaluator which has the context of previous two agents and also identifies if there is any missing information from the extraction phase from image if found any gaps of missing contents it will identifies the gap and sends back to extractor agent for extraction and maximum of two iterations happens here. 
  - if still extractions is not accurate the evalutor gets escalated to pro model and does the extraction and pass it as final response.
  - to make the extraction better and powerful 
