<img src="./tu5to02r.png"
style="width:0.28299in;height:0.28232in" /><img src="./4wfygpov.png"
style="width:0.9878in;height:0.17014in" />

**DesigninganExcel/CSVDataMappingAgent** **withGoogleGeminiLLM**

We need an AI-powered agent (e.g. a Jupyter notebook + simple UI) that
**ingests** **Excel/CSV** **files**, automatically maps their columns to
a fixed target schema, allows optional schema changes, and exports a
cleaned Excel with results and logs. Key requirements include using
Google’s Gemini API (Flash/Pro 2.5) on the free tier, asking the human
for clarification when needed, and handling special
cases(likecombiningseparateday/month/yearintoasingledatecolumn).Belowweoutlineathorough
designcoveringingestion,mapping,transformation,LLMintegration,UI,andoutputwithlogging.

**1.DataIngestion(Excel/CSV)andSchemaRecognition**

First, the agent should accept both **CSV** **and** **Excel** inputs. In
Python (Jupyter), one typically uses pandas.read_csv() or
pandas.read_excel() to load the file into a DataFrame
[1](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=You%20can%20use%20pandas%20to,in%20CSV%20and%20Excel%20files)
. A minimal UI (for example, a Streamlit app or an IPyWidgets-based
notebook) can use a **file** **uploader** **widget** so the user can
select a file. For example, Streamlit’s st.file_uploader will send the
uploaded file as a binary buffer to Python, which pd.read_csv() or
pd.read_excel() can directly consume
[2](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=There%E2%80%99s%20a%20more%20interactive%20way,how%20to%20deal%20with%20them)
. Once loaded, the agent should inspect the DataFrame’s shape (number of
rows/columns) and show the header names. This lets the agent *discover*
*the* *structure* of the data. As one reference notes, modern LLMs like
Google Gemini can even read the spreadsheet structure (headers, data
types) from the file

andunderstanditscontents
[3](https://www.datastudios.org/post/google-gemini-spreadsheet-reading-formats-analysis-depth-and-workspace-integration#:~:text=within%20the%20Gemini%20app%2C%20Google,comparison%20inside%20the%20conversational%20interface)
– ausefulparalleltoourparsingstep.

After loading, the agent compares the input columns against the
**fixed** **target** **schema**. The schema is “fixed and final” for
now, but the agent should be flexible: if it finds extra columns not in
the schema (or missing expected ones), it should flag them. For example,
the agent can present any unmapped columns to the user (in the UI) and
ask: *“This* *file* *has* *extra* *column* *XYZ* *–* *should* *this*
*be* *added* *to* *the* *schema,* *ignored,* *or* *mapped* *to* *an*
*existing* *field?”* This human-in-the-loop check ensures no data is
silently dropped.

**2.ColumnMappingandRenaming**

With the schema in mind, the agent must **map/rename** **the**
**incoming** **columns** **to** **the** **standard** **names**. In
pandas this is straightforward: provide a dictionary of old-to-new names
to

> df.rename(columns=...) .Forinstance:
>
> mapping = {'oldNameA': 'TargetA', 'oldNameB': 'TargetB', ...} df =
> df.rename(columns=mapping)

Thiswillrelabelonlythosecolumnsspecified,leavingothercolumnsas-is
[4](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.rename.html#:~:text=Rename%20columns%20using%20a%20mapping%3A)
.Unmappedcolumns
(eitherextraorunexpectedones)canremainfornowandbedealtwith(asnotedabove)byaskingthe
userwhethertodroporincorporatethem.

Because column names can vary widely, we can **leverage** **the**
**LLM** **to** **suggest** **mappings**. For example, send each raw
column name (or a few examples of its values) to Google Gemini and ask:
“Which target

> 1

field does this column most closely match?” If the LLM is confident (or
the top matches are clear), apply the rename. If the LLM is uncertain or
multiple schema fields are close, flag for user confirmation. This
follows an “AI-assisted mapping” approach: the LLM speeds up detection,
and the user verifies any ambiguous cases. (In practice, one might frame
a prompt like: *“I* *have* *a* *column* *named* *‘SalesAmt’.* *Which*
*standardizedcolumnfrom\[Revenue,Spend,Impressions,…\]isitlikelytobe?”*TheLLMcananswerbasedon
semantics, then we rename accordingly.) If entirely new columns appear,
the agent can propose adding themandasktheusertoconfirmordropthem.

**3.DataTransformation(Unpivoting,DateHandling,etc.)**

Many data files require reshaping before matching the schema. For
example, if the raw data is in a
**pivoted(wide)format**,weshould**unpivot(melt)**ittolongformatsoitalignswiththetargetlayout.In
pandas, this is done with pd.melt() . For example, if you have monthly
columns like Jan2023 , Feb2023 , etc., you can unpivot them into a
single “Month” column and a “Value” column. As

documented:

> “In pandas, you can use the melt() function to unpivot a DataFrame –
> converting it fromawideformattoalongformat”
> [5](https://www.statology.org/pandas-unpivot/#:~:text=In%20pandas%2C%20you%20can%20use,format%20to%20a%20long%20format)
> .

Forinstance:

> df_long = pd.melt(df, id_vars=\['Category','SubCategory'\],
> value_vars=\['Jan2023','Feb2023',...\], var_name='Month',
> value_name='Metric')

Thisstacksthosemonthcolumnsintoonecolumnnamed“Month”withthecorrespondingvaluesin
“Metric”.

Another common transformation is combining separate **date** **parts**.
If the file has columns day , month , and year , the agent should
combine them into one datetime column. Pandas makes this

easy:

> df\['Date'\] = pd.to_datetime(dict(year=df.year, month=df.month,
> day=df.day))

Asonetutorialnotes,thiswillcreateproper datetime64 datesfromtheparts
[6](https://www.statology.org/pandas-create-date-column-from-year-month-day/#:~:text=You%20can%20use%20the%20following,columns%20in%20a%20pandas%20DataFrame)
.Forexample, year=2022, month=5, day=27 wouldbecome 2022-05-27
.Aftercombining,theoriginalday/

month/yearcolumnscanbedropped.Theagentshouldknowthisrule(e.g.anytimeallthreepartsare
present,mergethem)andhandleitautomatically.

For other transformations (e.g. filling missing values, converting
types, normalizing categories), simple pandas operations or validation
tools can be used. The notes mention possibly using **Pydantic** for
type validation (“structured tool”) so that after renaming, the data
types match expectations (e.g. numeric vs
string).Thiswouldbealayertocatchif,say,acolumnexpectedtobenumericactuallycontainstext;the
agentcouldthenprompttheuserorattemptaconversion.

> 2

**4.IntegratingGoogleGemini(Flash/Pro2.5)**

The agent should use the **Google** **Gemini** **LLM** (specifically the
2.5 Flash or Pro model) via API. Google offers a free tier for Gemini:
both the 2.5 Flash and Pro models allow a significant amount of free
token usage (e.g., input and output are free up to a limit per day)
[7](https://ai.google.dev/gemini-api/docs/pricing#:~:text=Free%20Tier%20Paid%20Tier%2C%20per,limit%20shared%20with)
. To use it, set up a Google API key for Gemini (the console lets you
create a **Gemini** **API** **key** at no cost to start)
[8](https://ai.google.dev/gemini-api/docs/quickstart#:~:text=Using%20the%20Gemini%20API%20requires,for%20free%20to%20get%20started)
. In Python, Google
providesaGenAIclientlibrary.Forexample,afterinstalling google-genai
,oneinitializes:

> from google import genai
>
> client = genai.Client() \# reads GEMINI_API_KEY from environment
> response = client.models.generate_content(
>
> model="gemini-2.5-flash", contents="Your prompt here..."
>
> ) print(response.text)

(Theoficialdocsshowusageof
client.models.generate_content(model="gemini-3-flash-preview", ...)
,butthesameinterfaceworksfor2.5Flash/Proonceyouspecifythatmodelstring

[9](https://ai.google.dev/gemini-api/docs/quickstart#:~:text=from%20google%20import%20genai)
.)Bysendingwell-craftedpromptsthatdescribethemappingtaskoraskforclarification,the
Geminimodelcanhelpinterpretcolumnmeaningsorevensuggesthowtohandledatavalues.Since
Geminiisdesignedtounderstandtabulardata(it“automaticallyrecognizescolumnstructures”from
CSV/Excel
[3](https://www.datastudios.org/post/google-gemini-spreadsheet-reading-formats-analysis-depth-and-workspace-integration#:~:text=within%20the%20Gemini%20app%2C%20Google,comparison%20inside%20the%20conversational%20interface)
),itshouldbeadeptatthisjob.

Keepusagemoderateduetoratelimits:thefreetierallowsacertainnumberofrequestsperday(e.g.a
few million tokens)
[7](https://ai.google.dev/gemini-api/docs/pricing#:~:text=Free%20Tier%20Paid%20Tier%2C%20per,limit%20shared%20with)
. But since the task is mainly mapping a relatively small schema (and
not analyzinghugetext),itshouldfitcomfortablyunderfreelimits.

**5.UserInterfaceforInteraction**

For demonstrating and interacting with the agent, a simple UI is useful.
In Jupyter, one could use **IPyWidgets** to build forms, but a popular
alternative is to use **Streamlit** (or Voila) to create a lightweight
web app around the notebook code. For example, Streamlit allows placing
a file uploader ( st.file_uploader ), some drop-downs or checkboxes for
confirming column mappings, and data display with st.dataframe or
st.write . As one tutorial notes, after loading the data with pandas,
youcansimplycall st.write(df) todisplaytheDataFrameintheapp
[10](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=uploaded_file%20%3D%20st.file_uploader%28,write%28dataframe)
.

A basic Flow in the UI might be: 1. **File** **upload**: User uploads an
Excel/CSV file. 2. **Column** **preview**: Show the read column names
and a few sample rows. 3. **Mapping** **suggestions**: Display which
input columns will be mapped to which standard columns (perhaps
auto-filled by the agent), with options to edit or confirm. 4.
**Process**: User clicks “Process” or “Run”. The agent applies renaming
and transformations. 5. **Results** **display**: Show the cleaned data
(or a summary of changes) and any flagged
issues.6.**Output**:ProvideadownloadlinkforthefinalExcel.

This provides an “intuitive” interface while the heavy lifting is done
by Python in the background. The st.file_uploader example demonstrates
exactly how Streamlit passes the file into pandas (and

handlesrerunningwhenanewfileisuploaded)
[1](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=You%20can%20use%20pandas%20to,in%20CSV%20and%20Excel%20files)
[2](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=There%E2%80%99s%20a%20more%20interactive%20way,how%20to%20deal%20with%20them)
.

> 3

**6.ExportingResultsandLogs**

After processing, we export the results back to Excel. Using pandas, one
can write to Excel with DataFrame.to_excel()
.Toincludeboththeprocesseddataandaseparatelogsheetinonefile,use

an ExcelWriter .Forexample:

> writer = pd.ExcelWriter('output.xlsx', engine='xlsxwriter')
> df_processed.to_excel(writer, sheet_name='Results', index=False)
> df_logs.to_excel(writer, sheet_name='Logs', index=False) writer.save()

Asthepandasdocumentationexplains,writingtomultiplesheetsrequirescreatingan
ExcelWriter andspecifying sheet_name foreachDataFrame
[11](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_excel.html#:~:text=To%20write%20a%20single%20object,the%20file%20to%20write%20to)
.Thissatisfiesrequirement(5):thefinalexported
Excel(or“workbook”)willhaveonesheetwiththedataanswers(thecleaned/mappedrows)and
anothersheetwithanylogsornotesaboutwhatwasdone.

If desired, the agent can also display some log info in the UI (e.g.
“Renamed columns: A→a, B→c; Combined date from year/month/day; Filled 5
missing values in X”). But having the logs in a sheet
ensuresalldetailsaresavedandreviewablelater.

**7.Human-in-the-LoopClarification**

Finally, the agent should defer to the user whenever its confidence is
low. Whenever the LLM or the program logic encounters ambiguity (e.g. a
column name that could map to multiple schema fields, or unexpected data
formats), the agent should ask the user for clarification via the UI.
For example, if the LLM sees “Cost” and the schema has both “Spends” and
“Impressions”, it might ask *“Does* *‘Cost’* *refer* *to* *advertising*
*spend* *or* *something* *else?”* If the agent is coded to recognize the
pattern “day, month, year” and automatically combine them, it should do
so quietly; but if it sees unusual column headers or
values(e.g.adateintext),itmightprompttheusertoconfirm.

This human-AI collaboration ensures accuracy. In practice, you could
implement a rule: if the LLM’s top suggestion for mapping has low score
(or multiple equally likely), pop up a confirmation dialog or selection
box. The logs would note any such queries (“User confirmed mapping of
column X to Y”). Such aloopkeepstheagent“smartbutcautious”asrequired.

**Sources:** We use pandas for reading/writing Excel/CSV and reshaping
data (e.g. pd.read_excel/ csv , df.rename , pd.melt() , pd.to_datetime
[1](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=You%20can%20use%20pandas%20to,in%20CSV%20and%20Excel%20files)
[5](https://www.statology.org/pandas-unpivot/#:~:text=In%20pandas%2C%20you%20can%20use,format%20to%20a%20long%20format)
[6](https://www.statology.org/pandas-create-date-column-from-year-month-day/#:~:text=You%20can%20use%20the%20following,columns%20in%20a%20pandas%20DataFrame)
). Google’s Gemini API (2.5 Flash/Pro) can be accessed with a free API
key
[8](https://ai.google.dev/gemini-api/docs/quickstart#:~:text=Using%20the%20Gemini%20API%20requires,for%20free%20to%20get%20started)
and used via the Python GenAI client ( genai.Client ) to call
generate_content on the model
[9](https://ai.google.dev/gemini-api/docs/quickstart#:~:text=from%20google%20import%20genai)
. The Gemini model is explicitly designed to parse spreadsheets (“reads
files such as CSV and Excel… and automatically recognizes column
structures”
[3](https://www.datastudios.org/post/google-gemini-spreadsheet-reading-formats-analysis-depth-and-workspace-integration#:~:text=within%20the%20Gemini%20app%2C%20Google,comparison%20inside%20the%20conversational%20interface)
). Finally, pandas’ Excel writer allows saving multiple sheets in one
workbook
[11](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_excel.html#:~:text=To%20write%20a%20single%20object,the%20file%20to%20write%20to)
. All these tools together enable the agent to meet the requirements:
fixed schema with optional changes, CSV/Excel I/O, Google LLM

integration(freetier),asimpleUIforuploadingdata,andexportinganswerspluslogsinanExcelfile.

[1](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=You%20can%20use%20pandas%20to,in%20CSV%20and%20Excel%20files)
[2](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=There%E2%80%99s%20a%20more%20interactive%20way,how%20to%20deal%20with%20them)
[10](https://discuss.streamlit.io/t/loading-our-own-datasets/7118#:~:text=uploaded_file%20%3D%20st.file_uploader%28,write%28dataframe)
Loadingourowndatasets-Random-Streamlit
<https://discuss.streamlit.io/t/loading-our-own-datasets/7118>

> 4

[3](https://www.datastudios.org/post/google-gemini-spreadsheet-reading-formats-analysis-depth-and-workspace-integration#:~:text=within%20the%20Gemini%20app%2C%20Google,comparison%20inside%20the%20conversational%20interface)
GoogleGemini—
SpreadsheetReading:formats,analysisdepth,andWorkspaceintegration
<https://www.datastudios.org/post/google-gemini-spreadsheet-reading-formats-analysis-depth-and-workspace-integration>

[4](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.rename.html#:~:text=Rename%20columns%20using%20a%20mapping%3A)
pandas.DataFrame.rename— pandas3.0.1documentation
<https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.rename.html>

[5](https://www.statology.org/pandas-unpivot/#:~:text=In%20pandas%2C%20you%20can%20use,format%20to%20a%20long%20format)
HowtoUnpivotaPandasDataFrame(WithExample)
<https://www.statology.org/pandas-unpivot/>

[6](https://www.statology.org/pandas-create-date-column-from-year-month-day/#:~:text=You%20can%20use%20the%20following,columns%20in%20a%20pandas%20DataFrame)
Pandas:CreateDateColumnfromYear,MonthandDay
<https://www.statology.org/pandas-create-date-column-from-year-month-day/>

[7](https://ai.google.dev/gemini-api/docs/pricing#:~:text=Free%20Tier%20Paid%20Tier%2C%20per,limit%20shared%20with)
GeminiDeveloperAPIpricing \| GeminiAPI \| GoogleAIforDevelopers
<https://ai.google.dev/gemini-api/docs/pricing>

[8](https://ai.google.dev/gemini-api/docs/quickstart#:~:text=Using%20the%20Gemini%20API%20requires,for%20free%20to%20get%20started)
[9](https://ai.google.dev/gemini-api/docs/quickstart#:~:text=from%20google%20import%20genai)
GeminiAPIquickstart \| GoogleAIforDevelopers
<https://ai.google.dev/gemini-api/docs/quickstart>

[11](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_excel.html#:~:text=To%20write%20a%20single%20object,the%20file%20to%20write%20to)
pandas.DataFrame.to_excel— pandas3.0.1documentation
<https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_excel.html>

> 5
